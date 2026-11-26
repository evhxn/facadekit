<?php
/*
 * PJLink class 1 client in plain PHP sockets (no extensions, no CPAN, no pip).
 *
 * Protocol (JBMIA PJLink specification, class 1), port 4352/TCP:
 *   on connect the projector says  "PJLINK 0"            (no authentication) or
 *                                  "PJLINK 1 <random>"   (authentication on);
 *   a command is "%1<CMD> <param>\r", with authentication prefixed by
 *   md5(<random> . <password>) as 32 lower-case hex digits on the first command;
 *   the answer is "%1<CMD>=<value>\r", or "PJLINK ERRA" when the digest is wrong;
 *   value ERR1 = undefined command, ERR2 = bad parameter, ERR3 = unavailable now
 *   (warming up, cooling down, switching), ERR4 = projector failure.
 *
 * One command per TCP connection: some projectors (several Panasonic models among them) close
 * the socket after each answer, and a fresh connection costs a few milliseconds on a wired
 * LAN. Every call has a connect timeout and an overall read deadline. The password is only
 * ever used to compute the digest; it is never logged or put into an exception message.
 */

if (defined('FK_PLUGIN_PJLINK')) {
    return;
}
define('FK_PLUGIN_PJLINK', 1);

final class FkPjlinkException extends RuntimeException
{
    /** connect | timeout | closed | auth | protocol | undefined | parameter | unavailable | failure */
    public string $kind;

    public function __construct(string $kind, string $message)
    {
        parent::__construct($message);
        $this->kind = $kind;
    }
}

final class FkPjlink
{
    public const POWER_STATES = array('0' => 'standby', '1' => 'on', '2' => 'cooling', '3' => 'warmup');
    public const ERST_FIELDS = array('fan', 'lamp', 'temperature', 'cover', 'filter', 'other');
    public const ERST_LEVELS = array('0' => 'ok', '1' => 'warning', '2' => 'error');

    private string $host;
    private int $port;
    private ?string $password;
    private float $connectTimeout;
    private float $ioTimeout;
    /** Commands sent, without digests (for diagnostics). */
    public array $sent = array();

    public function __construct(string $host, int $port = 4352, ?string $password = null,
                                float $connectTimeout = 3.0, float $ioTimeout = 5.0)
    {
        $this->host = $host;
        $this->port = $port;
        $this->password = ($password === '') ? null : $password;
        $this->connectTimeout = $connectTimeout;
        $this->ioTimeout = $ioTimeout;
    }

    // -- high level ---------------------------------------------------------------------

    /** 'standby' | 'on' | 'cooling' | 'warmup' */
    public function power(): string
    {
        $value = $this->command('POWR', '?');
        if (!isset(self::POWER_STATES[$value])) {
            throw new FkPjlinkException('protocol', 'unexpected power state ' . $this->clip($value));
        }
        return self::POWER_STATES[$value];
    }

    public function powerOn(): void
    {
        $this->expectOk($this->command('POWR', '1'), 'POWR 1');
    }

    public function powerOff(): void
    {
        $this->expectOk($this->command('POWR', '0'), 'POWR 0');
    }

    public function input(): string
    {
        return $this->command('INPT', '?');
    }

    public function setInput(string $input): void
    {
        if (!preg_match('/^[1-9][0-9a-zA-Z]$/', $input)) {
            throw new FkPjlinkException('parameter', 'input must be two characters such as 31');
        }
        $this->expectOk($this->command('INPT', $input), 'INPT ' . $input);
    }

    /** array field => 'ok' | 'warning' | 'error' */
    public function errorStatus(): array
    {
        $value = $this->command('ERST', '?');
        if (!preg_match('/^[0-2]{6}$/', $value)) {
            throw new FkPjlinkException('protocol', 'unexpected error status ' . $this->clip($value));
        }
        $out = array();
        foreach (self::ERST_FIELDS as $i => $field) {
            $out[$field] = self::ERST_LEVELS[$value[$i]];
        }
        return $out;
    }

    /** List of array('hours' => int, 'on' => bool), one per lamp or light source. */
    public function lampHours(): array
    {
        $value = $this->command('LAMP', '?');
        $parts = preg_split('/\s+/', trim($value));
        if ($parts === false || count($parts) < 2 || count($parts) % 2 !== 0) {
            throw new FkPjlinkException('protocol', 'unexpected lamp reply ' . $this->clip($value));
        }
        $out = array();
        for ($i = 0; $i + 1 < count($parts); $i += 2) {
            if (!preg_match('/^[0-9]{1,9}$/', $parts[$i]) || ($parts[$i + 1] !== '0' && $parts[$i + 1] !== '1')) {
                throw new FkPjlinkException('protocol', 'unexpected lamp reply ' . $this->clip($value));
            }
            $out[] = array('hours' => (int)$parts[$i], 'on' => $parts[$i + 1] === '1');
        }
        return $out;
    }

    public function name(): string
    {
        return $this->command('NAME', '?');
    }

    public function manufacturer(): string
    {
        return $this->command('INF1', '?');
    }

    public function model(): string
    {
        return $this->command('INF2', '?');
    }

    public function otherInfo(): string
    {
        return $this->command('INFO', '?');
    }

    public function pjlinkClass(): string
    {
        return $this->command('CLSS', '?');
    }

    // -- one exchange -------------------------------------------------------------------

    /**
     * Send "%1<cmd> <param>" and return the value after "=". ERR1..ERR4 become exceptions of
     * kind undefined / parameter / unavailable / failure.
     */
    public function command(string $cmd, string $param): string
    {
        $cmd = strtoupper($cmd);
        if (!preg_match('/^[A-Z0-9]{4}$/', $cmd) || strlen($param) > 128 || preg_match('/[\r\n]/', $param)) {
            throw new FkPjlinkException('parameter', 'bad PJLink command');
        }
        $deadline = microtime(true) + $this->connectTimeout + $this->ioTimeout;
        $sock = $this->connect();
        try {
            $greeting = $this->readLine($sock, $deadline);
            $prefix = $this->authPrefix($greeting);
            $this->write($sock, $prefix . '%1' . $cmd . ' ' . $param . "\r");
            $this->sent[] = $cmd . ' ' . $param;
            for ($lines = 0; $lines < 4; $lines++) {
                $line = $this->readLine($sock, $deadline);
                if (stripos($line, 'PJLINK ERRA') === 0) {
                    throw new FkPjlinkException('auth', 'the projector refused the password');
                }
                if (preg_match('/^%1([A-Za-z0-9]{4})=(.*)$/s', $line, $m) && strtoupper($m[1]) === $cmd) {
                    return $this->mapError(trim($m[2]), $cmd);
                }
                // Anything else (an echo, a stray notification) is skipped.
            }
            throw new FkPjlinkException('protocol', 'no answer to ' . $cmd);
        } finally {
            fclose($sock);
        }
    }

    private function mapError(string $value, string $cmd): string
    {
        switch (strtoupper($value)) {
            case 'ERR1':
                throw new FkPjlinkException('undefined', $cmd . ' is not supported by this projector');
            case 'ERR2':
                throw new FkPjlinkException('parameter', $cmd . ': parameter refused');
            case 'ERR3':
                throw new FkPjlinkException('unavailable', $cmd . ' is unavailable right now (warming up, cooling down or busy)');
            case 'ERR4':
                throw new FkPjlinkException('failure', 'the projector reports a failure (' . $cmd . ')');
        }
        return $value;
    }

    private function expectOk(string $value, string $what): void
    {
        if (strtoupper($value) !== 'OK') {
            throw new FkPjlinkException('protocol', $what . ': unexpected reply ' . $this->clip($value));
        }
    }

    private function authPrefix(string $greeting): string
    {
        if (stripos($greeting, 'PJLINK ERRA') === 0) {
            throw new FkPjlinkException('auth', 'the projector refused the connection (authentication error)');
        }
        if (preg_match('/^PJLINK\s+0\s*$/i', $greeting)) {
            return '';
        }
        if (preg_match('/^PJLINK\s+1\s+(\S+)\s*$/i', $greeting, $m)) {
            if ($this->password === null) {
                throw new FkPjlinkException('auth', 'the projector asks for a PJLink password; add it in the projector table');
            }
            return md5($m[1] . $this->password);
        }
        if (preg_match('/^PJLINK\s+2\b/i', $greeting)) {
            throw new FkPjlinkException('protocol', 'the projector uses PJLink class 2 authentication, which is not supported');
        }
        throw new FkPjlinkException('protocol', 'not a PJLink greeting: ' . $this->clip($greeting));
    }

    // -- sockets ------------------------------------------------------------------------

    private function connect()
    {
        $target = (strpos($this->host, ':') !== false ? '[' . $this->host . ']' : $this->host) . ':' . $this->port;
        $errno = 0;
        $errstr = '';
        $sock = @stream_socket_client('tcp://' . $target, $errno, $errstr, $this->connectTimeout);
        if ($sock === false) {
            $text = strtolower($errstr);
            if (strpos($text, 'timed out') !== false || $errno === 110) {
                throw new FkPjlinkException('timeout', 'no answer from ' . $target . ' within ' . $this->connectTimeout . ' s');
            }
            if (strpos($text, 'refused') !== false || $errno === 111) {
                throw new FkPjlinkException('connect', $target . ' refused the connection (is PJLink turned on in the projector menu?)');
            }
            if (strpos($text, 'no route') !== false || $errno === 113 || $errno === 101) {
                throw new FkPjlinkException('connect', 'no route to ' . $target . ' (check the cable and the Pi\'s wired address)');
            }
            throw new FkPjlinkException('connect', 'could not connect to ' . $target . ($errstr !== '' ? ' (' . $errstr . ')' : ''));
        }
        stream_set_blocking($sock, true);
        return $sock;
    }

    private function readLine($sock, float $deadline): string
    {
        $buf = '';
        while (true) {
            $left = $deadline - microtime(true);
            if ($left <= 0) {
                throw new FkPjlinkException('timeout', 'the projector did not answer in time');
            }
            $sec = (int)floor($left);
            stream_set_timeout($sock, $sec, (int)(($left - $sec) * 1000000));
            $char = fread($sock, 1);
            if ($char === false || $char === '') {
                $meta = stream_get_meta_data($sock);
                if (!empty($meta['timed_out'])) {
                    throw new FkPjlinkException('timeout', 'the projector did not answer in time');
                }
                if (feof($sock)) {
                    if ($buf !== '') {
                        return $buf;
                    }
                    throw new FkPjlinkException('closed', 'the projector closed the connection');
                }
                continue;
            }
            if ($char === "\r" || $char === "\n") {
                if ($buf === '') {
                    continue;
                }
                return $buf;
            }
            if (ord($char) < 0x20 || ord($char) === 0x7f) {
                continue; // NUL padding and other control bytes some firmwares send
            }
            $buf .= $char;
            if (strlen($buf) > 512) {
                throw new FkPjlinkException('protocol', 'answer too long');
            }
        }
    }

    private function write($sock, string $data): void
    {
        $sent = 0;
        $len = strlen($data);
        while ($sent < $len) {
            $n = @fwrite($sock, substr($data, $sent));
            if ($n === false || $n === 0) {
                throw new FkPjlinkException('closed', 'the projector closed the connection');
            }
            $sent += $n;
        }
    }

    private function clip(string $value): string
    {
        $value = preg_replace('/[^\x20-\x7e]/', '?', $value);
        return '"' . (strlen($value) > 40 ? substr($value, 0, 40) . '...' : $value) . '"';
    }
}
