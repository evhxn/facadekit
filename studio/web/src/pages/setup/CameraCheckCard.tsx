import type { CameraCheck } from "../../api/types";
import styles from "../SetupPage.module.css";
import { COPY, describeCheck, type CheckButton } from "./cameraCopy";

interface Props {
  check: CameraCheck;
  /** Opens the crop box on the suggested crop. */
  onCrop: () => void;
  onUsePhone: () => void;
  /** Go on to the scan step. */
  onNext: () => void;
  onRecheck: () => void;
}

export default function CameraCheckCard({ check, onCrop, onUsePhone, onNext, onRecheck }: Props) {
  const text = describeCheck(check);
  const actions: Record<CheckButton, () => void> = {
    continue: onNext,
    scan: onNext,
    scanAnyway: onNext,
    crop: onCrop,
    phone: onUsePhone,
    recheck: onRecheck,
  };
  return (
    <div className={styles.checkCard} data-verdict={check.verdict} role="status">
      <p className={styles.checkHeadline}>{text.headline}</p>
      {text.details ? <p>{text.details}</p> : null}
      {text.extras.map((line) => (
        <p key={line}>{line}</p>
      ))}
      <div className={styles.row}>
        {text.buttons.map((id, i) => (
          <button
            key={id}
            type="button"
            className={i === 0 ? "primary" : undefined}
            onClick={actions[id]}
          >
            {COPY.buttons[id]}
          </button>
        ))}
      </div>
    </div>
  );
}
