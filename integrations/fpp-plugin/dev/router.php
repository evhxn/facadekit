<?php
/*
 * Development router for PHP's built-in server: serves the plugin's REST API without FPP,
 *   FK_PLUGIN_DATA_DIR=/tmp/fk FK_PLUGIN_LOG=/tmp/fk/plugin.log \
 *     php -S 127.0.0.1:8099 dev/router.php
 * and then /api/plugin/fpp-plugin-FacadeKit/<route>. Used by the backend tests when php is
 * installed. FPP's other routes are not served here.
 */
require_once dirname(__DIR__) . '/lib/api_core.php';

$path = (string)parse_url((string)($_SERVER['REQUEST_URI'] ?? '/'), PHP_URL_PATH);
$prefix = '/api/plugin/' . FK_REPO . '/';
if (strpos($path, $prefix) !== 0) {
    http_response_code(404);
    header('Content-Type: text/plain');
    echo "not found\n";
    return true;
}
$route = rawurldecode(substr($path, strlen($prefix)));
$method = (string)($_SERVER['REQUEST_METHOD'] ?? 'GET');
$body = in_array($method, array('POST', 'PUT'), true) ? (string)file_get_contents('php://input') : '';
list($status, $payload) = fk_api_handle($method, $route, $_SERVER, $body);
http_response_code($status);
header('Content-Type: application/json');
echo json_encode($payload, JSON_UNESCAPED_SLASHES);
return true;
