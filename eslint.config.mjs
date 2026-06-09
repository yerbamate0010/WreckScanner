import js from "@eslint/js";

export default [
  {
    ignores: ["analiza/**", "node_modules/**"],
  },
  {
    files: ["web/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
    },
    rules: {
      ...js.configs.recommended.rules,
      "no-console": "off",
      "no-undef": "off",
      "no-unused-vars": "warn",
    },
  },
];
