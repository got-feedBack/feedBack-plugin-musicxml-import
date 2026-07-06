/** Plugin stylesheet build — utilities only, scanned from this plugin's files.
 *  Regenerate assets/plugin.css with: bash build-tailwind.sh                */
module.exports = {
  corePlugins: { preflight: false }, // core owns the single base reset
  content: [
    './screen.html',
    './screen.js',
  ],
  theme: {
    extend: {
      // Re-declare the core theme tokens this plugin references so they
      // compile here (mirrors core's tailwind.config.js).
      colors: {
        dark: { 900: '#050508', 800: '#0a0a12', 700: '#10101e', 600: '#181830', 500: '#1e1e3a' },
        accent: { DEFAULT: '#4080e0', light: '#60a0ff', dark: '#2060b0' },
        gold: '#e8c040',
      },
    },
  },
  plugins: [],
};
