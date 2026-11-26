<?php
# One menu entry (PLUGIN_GUIDELINES.md 9.1: at most one per area). The page holds the
# projector table, the automatic power options, the live status, the shows and the log.
$menuEntries = array(
    array(
        'text' => 'FacadeKit Projectors',
        'type' => 'status',
        'page' => 'setup.php',
        'wrap' => 1,
    ),
);

foreach ($menuEntries as $entry) {
    if ($entry['type'] != $menu) {
        continue;
    }
    $nopage = (isset($entry['wrap']) && $entry['wrap'] == 0) ? '&nopage=1' : '';
    printf("<li><a href='plugin.php?plugin=%s&page=%s%s'>%s</a></li>\n",
        htmlspecialchars($plugin, ENT_QUOTES), $entry['page'], $nopage, htmlspecialchars($entry['text']));
}
