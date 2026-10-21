# Third-party notices

The original application code and new documentation are MIT licensed. Vendored
browser bundles retain their upstream notices and licenses:

| Asset | Upstream | License |
|---|---|---|
| `static/marked.min.js` | [Marked](https://github.com/markedjs/marked), bundle header v18.0.9 | MIT, see `licenses/marked-LICENSE` |
| `static/mermaid.min.js` | [Mermaid](https://github.com/mermaid-js/mermaid), vendored v11.16.1 | MIT, see `licenses/mermaid-LICENSE` |

The Mermaid bundle includes upstream dependency notices at its end, including
DOMPurify, js-yaml, lodash and Cytoscape-related code. Those notices are preserved
verbatim in the bundle. DOMPurify's license text is in `licenses/DOMPurify-LICENSE`;
its upstream offers Apache-2.0 or MPL-2.0. The application license does not replace
these dependency licenses. No runtime CDN fetch is required.
