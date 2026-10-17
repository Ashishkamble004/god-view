import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      colors: {
        canvas: {
          DEFAULT: '#FFFFFF',
          subtle: '#F8FAFC',
          muted: '#F1F5F9',
        },
        ink: {
          heading: '#0F172A',
          body: '#334155',
          muted: '#64748B',
        },
        accent: {
          blue: '#1A73E8',
          blueLight: '#4285F4',
          green: '#188038',
          greenLight: '#34A853',
          amber: '#F9AB00',
          amberLight: '#FBBC04',
          red: '#D93025',
          redLight: '#EA4335',
        },
      },
    },
  },
  plugins: [],
};

export default config;
