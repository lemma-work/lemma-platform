import tseslint from 'typescript-eslint';
import hooks from 'eslint-plugin-react-hooks';

/* `typescript-eslint` is here as a parser first and a plugin second: this is a
   deliberately small rule set, not a preset. Two rules earn their place because
   each catches something no other check in this repo can see.

   NOT enabled, and recorded rather than omitted: `react-hooks/exhaustive-deps`.
   It reports twelve sites, and several of them are deliberate — `use-huddle.ts`
   omits `backend` and `agents-view.tsx` keys a draft on the agent rather than on
   the fetched object, both for reasons written down beside them. Turning it on
   means either changing twelve hooks, which is a behaviour change dressed as a
   lint fix, or twelve disable comments. Worth doing as its own pass, with the
   dependency arrays actually read. */
const unused = ['error', {
    /* `const { is_active: _dropped, ...without } = wire` is how a test omits a
       key. Both halves of that idiom are named here so the rule does not turn
       a deliberate omission into an error. */
    varsIgnorePattern: '^_',
    argsIgnorePattern: '^_',
    ignoreRestSiblings: true,
}];

export default [{
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: { parser: tseslint.parser, parserOptions: { ecmaFeatures: { jsx: true } } },
    plugins: { 'react-hooks': hooks, '@typescript-eslint': tseslint.plugin },
    rules: {
        'react-hooks/rules-of-hooks': 'error',
        '@typescript-eslint/no-unused-vars': unused,
    },
}, {
    /* The custom server, the checks and the tests had no rules at all applying
       to them — `npm run lint` named only `src`, and the config matched only
       `src`, so linting them was silent rather than clean. */
    files: ['server/**/*.mjs', 'scripts/**/*.mjs', 'tests/**/*.{ts,mjs}', 'server.mjs'],
    languageOptions: { parser: tseslint.parser },
    plugins: { '@typescript-eslint': tseslint.plugin },
    rules: { '@typescript-eslint/no-unused-vars': unused },
}];
