#! /bin/python3

import mistune
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters import HtmlFormatter

class MDRenderer(mistune.Renderer):
    def __init__(self):
        super(mistune.Renderer).__init__()

    # Code Hightlight function
    def block_code(self, code, lang):
        if not lang:
            return '\n<pre><code>%s</code></pre>\n' % mistune.escape(code)

        lexer = get_lexer_by_name(lang, strlpall=True)
        formatter = HtmlFormatter()
        return hightlight(code, lexer, formatter)
