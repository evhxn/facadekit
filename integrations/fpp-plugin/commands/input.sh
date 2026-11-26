#!/bin/bash
# FPP Command script (commands/descriptions.json). fppd runs it directly with the command's
# arguments as separate argv entries and only MEDIADIR, FPPDIR and SCRIPTDIR set (no PATH),
# so it uses absolute paths and passes "$@" through unchanged.
exec "${FK_PHP:-/usr/bin/php}" "${SCRIPTDIR:-${0%/*}}/../lib/cli.php" input "$@"
