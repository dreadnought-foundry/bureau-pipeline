import { useInitiatives } from "../../data/initiatives";
import { tokens } from "../../styles/tokens";

export function InitiativesPage() {
  const { initiatives, error } = useInitiatives();
  return (
    <main style={{ padding: tokens.space[5] }}>
      <h1 style={{ font: tokens.type.heading }}>Initiatives</h1>
      <ul>
        {initiatives.map((initiative) => (
          <li key={initiative.id}>{initiative.name}</li>
        ))}
      </ul>
    </main>
  );
}
