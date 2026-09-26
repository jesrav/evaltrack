import eslint from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  eslint.configs.recommended,
  tseslint.configs.recommendedTypeChecked,
  reactHooks.configs.flat.recommended,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    // A component renders from the data it is handed, never from the server,
    // so the static report can reuse it. App fetches and passes down.
    files: ["src/components/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["**/api"],
              message:
                "Components render from props alone, so the static report can reuse them. Fetch in App and pass the result down.",
            },
          ],
        },
      ],
    },
  },
  {
    // Config files themselves are not part of the tsconfig project.
    files: ["*.js", "*.ts"],
    extends: [tseslint.configs.disableTypeChecked],
  },
);
