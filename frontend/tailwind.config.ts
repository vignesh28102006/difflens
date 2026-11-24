import type { Config } from "tailwindcss";

export default {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#05070A",
        surface: "#0A0F1E",
        "surface-card": "#0F172A",
        border: "#1E293B",
        accent: {
          DEFAULT: "#37E6FF",
          hover: "#22D3EE",
        },
        risk: {
          high: "#EF4444",
          medium: "#F59E0B",
          low: "#10B981",
        },
      },
      fontFamily: {
        mono: ["var(--font-geist-mono)", "Menlo", "Consolas", "Courier New", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
