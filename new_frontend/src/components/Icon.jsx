/**
 * Thin wrapper around Material Symbols Outlined (loaded via Google Fonts in
 * index.html). Pass any symbol name, e.g. <Icon name="auto_awesome" />.
 */
export default function Icon({ name, size = 20, fill = false, className = '' }) {
  return (
    <span
      aria-hidden="true"
      className={`material-symbols-outlined inline-block select-none leading-none ${className}`}
      style={{
        fontSize: size,
        fontVariationSettings: `'FILL' ${fill ? 1 : 0}, 'wght' 400, 'GRAD' 0, 'opsz' 24`,
      }}
    >
      {name}
    </span>
  );
}
