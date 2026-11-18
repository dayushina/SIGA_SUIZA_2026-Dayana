import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'node_modules', 'public', '.vite', 'coverage']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^[A-Z_]' }],
      // Regla nueva de eslint-plugin-react-hooks v7 (orientada a React Compiler).
      // Marca el patron estandar "fetch en useEffect + setLoading(true)". Se deja como
      // advertencia para migrar gradualmente (p. ej. a un hook de datos) sin reescribir
      // de golpe todos los flujos de carga, lo que tendria alto riesgo de regresion.
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
  {
    // Archivos de configuracion: se ejecutan en Node, no en el navegador
    files: ['*.config.js'],
    languageOptions: { globals: globals.node },
  },
  {
    // Punto de entrada: monta la app, no exporta componentes (Fast Refresh no aplica)
    files: ['src/main.jsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
])
