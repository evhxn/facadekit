import Dialog from "./Dialog";
import DiagnosticsButton from "./DiagnosticsButton";
import { modKey } from "./keys";
import styles from "./HelpDialog.module.css";

export const STEPS: { name: string; what: string; time: string }[] = [
  {
    name: "Setup",
    what: "Connect the projector and a camera, then let FacadeKit scan the house. The scan is how it learns where each projector pixel lands.",
    time: "about 10 minutes",
  },
  {
    name: "Surfaces",
    what: "Mark the parts of the house to light up: a door, a window, a wall. Click on one with Magic select, or drag a rectangle onto it.",
    time: "a few minutes per surface",
  },
  {
    name: "Arrange",
    what: "Put videos, pictures or ready-made effects on those surfaces, and choose when each one plays. Press Play to watch it.",
    time: "as long as you like",
  },
  {
    name: "Publish",
    what: "Turn the show into one video file and send it to Falcon Player (FPP) on a Raspberry Pi, which plays it every night.",
    time: "a few minutes",
  },
];

type Row = [keys: string, what: string];

function shortcuts(): { title: string; rows: Row[] }[] {
  const m = modKey();
  return [
    {
      title: "Everywhere",
      rows: [
        ["?", "Open this help"],
        [`${m}+Z`, "Undo the last change"],
        [`${m}+Shift+Z`, "Redo"],
        ["Esc", "Close a dialog, or stop what you are drawing"],
      ],
    },
    {
      title: "Surfaces (click the picture first)",
      rows: [
        ["Drag a corner", "Move that corner"],
        ["Drag inside", "Move the whole surface"],
        ["Arrow keys", "Nudge the surface or the selected corner by 1 pixel (Shift: 10)"],
        ["Tab", "Select the next corner"],
        ["Delete", "Delete the selected surface"],
        ["Scroll, or + and −", "Zoom in and out"],
        ["Space + drag", "Move around when zoomed in"],
        ["0", "Reset the view"],
      ],
    },
    {
      title: "Arrange (click the timeline first)",
      rows: [
        ["Space", "Play or pause"],
        ["Drag a clip", "Move it in time, or to another layer"],
        ["Drag a clip's edge", "Make it start later or end sooner"],
        ["Double-click or Enter", "Open the clip's settings"],
        ["Delete", "Remove the selected clips"],
        [`${m}+D`, "Duplicate the selected clips"],
        [`${m}+scroll, or + and −`, "Zoom the timeline"],
        ["M", "Add a marker where the play line is"],
        ["Right-click a clip", "Cut it on the beat"],
      ],
    },
  ];
}

export default function HelpDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog title="How FacadeKit works" onClose={onClose} wide>
      <ol className={styles.steps}>
        {STEPS.map((s) => (
          <li key={s.name}>
            <strong>{s.name}</strong> <span className={styles.time}>({s.time})</span>
            <br />
            {s.what}
          </li>
        ))}
      </ol>
      <p className={styles.note}>
        Everything is saved as you go. The dot next to the project name says &quot;All changes
        saved&quot; when it is safe to close the window.
      </p>
      <h3>Keyboard shortcuts</h3>
      {shortcuts().map((g) => (
        <table key={g.title} className={styles.table}>
          <caption>{g.title}</caption>
          <tbody>
            {g.rows.map(([k, what]) => (
              <tr key={k}>
                <th scope="row">
                  <kbd>{k}</kbd>
                </th>
                <td>{what}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
      <h3>Something wrong?</h3>
      <p className={styles.note}>
        Copy the diagnostic info and paste it into your bug report. It holds no photos or videos.
      </p>
      <DiagnosticsButton />
    </Dialog>
  );
}
