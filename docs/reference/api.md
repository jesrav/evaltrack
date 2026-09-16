# API reference

What `import evaltrack` exports: the calls a marked test makes, the models a recorded run is made
of, the repository read path, and the exceptions. The marker itself is not a Python callable. Its
keyword arguments are in
[Configuration › Marker keyword arguments](../configuration.md#marker-keyword-arguments).

## `evaltrack`

<!-- prettier-ignore -->
::: evaltrack
    options:
      show_root_heading: false
      show_root_toc_entry: false

## Extension surface

A translator turns an eval runner's result into an `EvalRound`. Registering another runner allows
you to use that runner with evaltrack. See [Translators](../translators.md).

::: evaltrack.translators.register

::: evaltrack.translators.protocol.Translator
