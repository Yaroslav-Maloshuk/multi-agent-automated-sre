/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: "#0b1020",
        panel: "#111827",
        accent: "#22d3ee",
        danger: "#f87171",
        success: "#34d399",
        warn: "#fbbf24",
      },
      animation: {
        "pulse-line": "pulseLine 1.2s ease-in-out infinite",
      },
      keyframes: {
        pulseLine: {
          "0%, 100%": { strokeOpacity: 0.2 },
          "50%": { strokeOpacity: 1 },
        },
      },
    },
  },
  plugins: [],
};
