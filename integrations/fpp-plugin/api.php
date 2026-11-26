<?php
/*
 * FPP plugin API hook: FPP's API router (www/api/controllers/plugin.php, FPP 10.4) loads this
 * file and calls getEndpointsfpppluginFacadeKit(); each endpoint is served at
 * /api/plugin/fpp-plugin-FacadeKit/<endpoint>. The logic lives in lib/api_core.php.
 * Every function here carries the fpppluginFacadeKit prefix so it cannot clash with FPP's own.
 */

require_once __DIR__ . '/lib/api_core.php';

function getEndpointsfpppluginFacadeKit()
{
    $routes = array(
        array('GET', 'version', 'fpppluginFacadeKitVersion'),
        array('GET', 'projector-names', 'fpppluginFacadeKitProjectorNames'),
        array('GET', 'status', 'fpppluginFacadeKitStatus'),
        array('GET', 'health', 'fpppluginFacadeKitHealth'),
        array('POST', 'projectors/on', 'fpppluginFacadeKitOn'),
        array('POST', 'projectors/off', 'fpppluginFacadeKitOff'),
        array('GET', 'shows', 'fpppluginFacadeKitShows'),
        array('POST', 'shows', 'fpppluginFacadeKitRegisterShow'),
        array('DELETE', 'shows/:playlist', 'fpppluginFacadeKitDeleteShow'),
    );
    $result = array();
    foreach ($routes as $r) {
        $result[] = array('method' => $r[0], 'endpoint' => $r[1], 'callback' => $r[2]);
    }
    return $result;
}

function fpppluginFacadeKitRespond(string $method, string $route)
{
    $body = ($method === 'POST') ? (string)file_get_contents('php://input') : '';
    list($status, $payload) = fk_api_handle($method, $route, $_SERVER, $body);
    http_response_code($status);
    header('Cache-Control: no-store');
    return json($payload);
}

function fpppluginFacadeKitVersion() { return fpppluginFacadeKitRespond('GET', 'version'); }
function fpppluginFacadeKitProjectorNames() { return fpppluginFacadeKitRespond('GET', 'projector-names'); }
function fpppluginFacadeKitStatus() { return fpppluginFacadeKitRespond('GET', 'status'); }
function fpppluginFacadeKitHealth() { return fpppluginFacadeKitRespond('GET', 'health'); }
function fpppluginFacadeKitOn() { return fpppluginFacadeKitRespond('POST', 'projectors/on'); }
function fpppluginFacadeKitOff() { return fpppluginFacadeKitRespond('POST', 'projectors/off'); }
function fpppluginFacadeKitShows() { return fpppluginFacadeKitRespond('GET', 'shows'); }
function fpppluginFacadeKitRegisterShow() { return fpppluginFacadeKitRespond('POST', 'shows'); }

function fpppluginFacadeKitDeleteShow()
{
    $name = params('playlist');
    return fpppluginFacadeKitRespond('DELETE', 'shows/' . (is_string($name) ? $name : ''));
}
