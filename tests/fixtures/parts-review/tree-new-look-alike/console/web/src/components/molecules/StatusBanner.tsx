import type { ReactNode } from "react";

import { tokens } from "../../styles/tokens";

export interface StatusBannerProps {
  tone: "info" | "success" | "warning" | "danger";
  title: string;
  children?: ReactNode;
  onDismiss?: () => void;
}

export function StatusBanner({ tone, title, children, onDismiss }: StatusBannerProps) {
  return (
    <section
      role="status"
      style={{
        background: tokens.color[tone].surface,
        borderColor: tokens.color[tone].border,
        padding: tokens.space[3],
      }}
    >
      <strong style={{ font: tokens.type.label }}>{title}</strong>
      {children}
      {onDismiss && (
        <button type="button" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </section>
  );
}
