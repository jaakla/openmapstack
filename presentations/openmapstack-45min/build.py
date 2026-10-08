"""Bundle the presentation into one offline HTML file (standard library only)."""
import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build():
    images = {}
    for key in ['analysis', 'map', 'provenance', 'comparison', 'trips']:
        image = ROOT / 'assets' / f'bridges-{key}.png'
        images[key] = 'data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode('ascii')
    content = (ROOT / 'content.js').read_text(encoding='utf-8').replace('__IMAGES__', json.dumps(images))
    script = '\n'.join([
        (ROOT / 'i18n.js').read_text(encoding='utf-8'),
        content,
        (ROOT / 'content-en.js').read_text(encoding='utf-8'),
        (ROOT / 'app.js').read_text(encoding='utf-8'),
    ])
    # Prevent a future content edit from accidentally closing the script element.
    script = script.replace('</script', '<\\/script')
    template = (ROOT / 'viewer.html').read_text(encoding='utf-8')
    output = template.replace('__STYLES__', (ROOT / 'styles.css').read_text(encoding='utf-8')).replace('__APP__', script)
    (ROOT / 'index.html').write_text(output, encoding='utf-8')
    print(f'Built {ROOT / "index.html"} ({len(output.encode("utf-8")):,} bytes)')


if __name__ == '__main__':
    build()
