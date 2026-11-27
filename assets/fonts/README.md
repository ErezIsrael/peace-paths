# Webfont sources

These TTFs are **not deployed**. `python tools/build-fonts.py` subsets them into
`app/fonts/*.woff2` (Latin / Hebrew / Arabic only), which is what the site ships.

| Font | Script coverage | License |
|---|---|---|
| `Heebo[wght].ttf` | Hebrew + Latin | SIL Open Font License 1.1 |
| `NotoSansArabic[wdth,wght].ttf` | Arabic | SIL Open Font License 1.1 |
| `inter-variable.ttf` | Latin only — **no Hebrew/Arabic**, kept as a source archive | SIL OFL 1.1 |
| `space-grotesk-variable.ttf` | Latin only — **no Hebrew/Arabic**, kept as a source archive | SIL OFL 1.1 |

Both used fonts are OFL-1.1; see https://openfontlicense.org for terms.
