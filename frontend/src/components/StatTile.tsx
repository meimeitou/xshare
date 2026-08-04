interface StatTileProps {
  label: string;
  value: string;
  sub?: string;
  valueColor?: string;
}

export function StatTile({ label, value, sub, valueColor }: StatTileProps) {
  return (
    <div
      className="surface flex flex-col gap-1 p-4"
      style={{
        borderRadius: "var(--radius)",
      }}
    >
      <span
        className="text-xs"
        style={{ color: "var(--text-dim)", letterSpacing: "0.04em" }}
      >
        {label}
      </span>
      <span
        className="mono text-xl font-semibold leading-none"
        style={{ color: valueColor ?? "var(--text)" }}
      >
        {value}
      </span>
      {sub && (
        <span
          className="text-xs mono"
          style={{ color: "var(--text-muted)", letterSpacing: "0.02em" }}
        >
          {sub}
        </span>
      )}
    </div>
  );
}
