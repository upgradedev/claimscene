export function StepHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="mb-7">
      <h2 className="font-mono text-2xl font-semibold text-blueprint-text md:text-3xl">{title}</h2>
      <p className="mt-2 max-w-2xl text-sm text-blueprint-dim">{subtitle}</p>
    </div>
  );
}
