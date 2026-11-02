import type { ReactNode } from "react";

import { tokens } from "../../styles/tokens";

export interface NoticeProps {
  tone: "info" | "success" | "warning" | "danger";
  title: string;
  children?: ReactNode;
  onDismiss?: () => void;
}

export function Notice({ tone, title, children, onDismiss }: NoticeProps) {
  return (
    <div
      role="status"
      style={{
        background: tokens.color[tone].surface,
        borderColor: tokens.color[tone].border,
        padding: tokens.space[3],
        borderRadius: tokens.radius.md,
      }}
    >
      <strong style={{ font: tokens.type.label }}>{title}</strong>
      {children}
      {onDismiss && (
        <button type="button" onClick={onDismiss} aria-label="Dismiss">
          ×
        </button>
      )}
    </div>
  );
}
