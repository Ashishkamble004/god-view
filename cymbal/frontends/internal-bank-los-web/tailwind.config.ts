import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      colors: {
        canvas: '#FFFFFF',
        surface: '#F8FAFC',
        ink: {
          heading: '#0F172A',
          body: '#334155',
          muted: '#64748B',
        },
        gblue: {
          DEFAULT: '#1A73E8',
          bright: '#4285F4',
          tint: '#E8F0FE',
          border: '#D2E3FC',
        },
        ggreen: {
          DEFAULT: '#188038',
          bright: '#34A853',
          tint: '#E6F4EA',
          border: '#CEEAD6',
        },
        gamber: {
          DEFAULT: '#B06000',
          raw: '#F9AB00',
          bright: '#FBBC04',
          tint: '#FEF7E0',
          border: '#FEEFC3',
        },
        gred: {
          DEFAULT: '#D93025',
          bright: '#EA4335',
          tint: '#FCE8E6',
          border: '#FAD2CF',
        },
      },
    },
  },
  plugins: [],
};

export default config;
