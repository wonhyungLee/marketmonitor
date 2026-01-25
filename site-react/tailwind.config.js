/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        warmup: "#3498db",
        normal: "#2ecc71",
        defcon2: "#e67e22",
        defcon1: "#e74c3c",
      }
    },
  },
  plugins: [],
}
