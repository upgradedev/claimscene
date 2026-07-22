/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "1.5rem",
      screens: { "2xl": "1240px" },
    },
    extend: {
      colors: {
        // Forensic-blueprint palette: dark steel/graphite drafting board,
        // amber survey line-work, cyan technical annotations. Deliberately NOT
        // Cinemory's warm cinema-gold — this reads as an engineering drawing.
        // The base tones harmonise with the schematic renderer's own palette
        // (BG #0e1a22, grid #17303d, centre-line #d9a441, edge #4a6b80).
        steel: {
          950: "#070d12",
          900: "#0a141c",
          850: "#0e1a22",
          800: "#122430",
          700: "#17303d",
          600: "#1d3a4a",
          500: "#2a4a5c",
          400: "#4a6b80",
          300: "#5f7d8f",
        },
        amber: {
          200: "#f2dca6",
          300: "#ecc879",
          400: "#d9a441",
          500: "#c8983a",
          600: "#a87f2c",
        },
        cyan: {
          200: "#a5e8f5",
          300: "#7dd3ec",
          400: "#38bdf8",
          500: "#22b8d6",
        },
        blueprint: {
          text: "#cfe3ee",
          dim: "#5f7d8f",
          line: "#17303d",
        },
        border: "hsl(198 30% 24% / 0.55)",
        ring: "#38bdf8",
      },
      fontFamily: {
        sans: ['"Inter"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
        display: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      boxShadow: {
        panel: "0 18px 50px -24px rgba(0,0,0,0.85), 0 0 0 1px rgba(120,160,180,0.05)",
        "glow-amber": "0 0 28px -10px rgba(217,164,65,0.5)",
        "glow-cyan": "0 0 26px -10px rgba(56,189,248,0.45)",
      },
      backgroundImage: {
        // The drafting grid — a fine + coarse survey grid, drawn as a fixed
        // background so the whole app feels like a blueprint sheet.
        grid: "linear-gradient(rgba(90,130,150,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(90,130,150,0.06) 1px, transparent 1px)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "sweep": {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(100%)" },
        },
        "ping-soft": {
          "0%, 100%": { opacity: "0.5" },
          "50%": { opacity: "1" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.5s cubic-bezier(0.22,1,0.36,1) both",
        sweep: "sweep 1.6s ease-in-out infinite",
        "ping-soft": "ping-soft 2.5s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
