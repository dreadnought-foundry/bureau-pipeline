import { tokens } from "../../styles/tokens";

export interface StepperProps {
  steps: readonly string[];
  current: number;
  onSelect?: (index: number) => void;
}

export function Stepper({ steps, current, onSelect }: StepperProps) {
  return (
    <ol style={{ display: "flex", gap: tokens.space[2] }}>
      {steps.map((label, index) => (
        <li key={label}>
          <button
            type="button"
            aria-current={index === current ? "step" : undefined}
            onClick={() => onSelect?.(index)}
            style={{
              font: tokens.type.label,
              color: index === current ? tokens.color.info.text : tokens.color.neutral.text,
            }}
          >
            {label}
          </button>
        </li>
      ))}
    </ol>
  );
}
