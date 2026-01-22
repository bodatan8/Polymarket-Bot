/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'poly-green': '#00D395',
        'poly-red': '#FF6B6B',
        'poly-dark': '#0D1117',
        'poly-card': '#161B22',
        'poly-border': '#30363D',
      },
      fontFamily: {
        'mono': ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
