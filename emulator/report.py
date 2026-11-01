"""Small offline HTML view of the actual run; no external assets or scripts."""
from html import escape
from pathlib import Path


def write_html(result,path):
    path = Path(path)
    def h(value):
        return escape(str(value),quote=True)
    frame = result.get('lcd_image')
    screen = (f'<img src="{h(Path(frame).name)}" width="352" height="440" alt="Кадр виртуального LCD">'
              if frame else '<p>Прошивка ещё не записала пиксели в LCD.</p>')
    nor_state = (f'Виртуальная NOR сохранена в <code>{h(result["saved_flash"])}</code>.'
                 if result.get('saved_flash') else 'Этот прогон не сохранял виртуальную NOR на диск.')
    logs = ''.join(f'<tr><td>{i}</td><td><code>{h(v["lr"])}</code></td><td><pre>{h(v["format"])}</pre></td>'
                   f'<td><code>{h(", ".join(v["args"]))}</code></td></tr>' for i,v in enumerate(result['guest_logs'],1))
    transfers = ''.join(f'<li>{h(v["source"])} → {h(v["target"])}: {v["count"]} байт</li>' for v in result['dma_transfers'])
    text = f'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>B220 — запуск прошивки</title>
<style>body{{font:16px system-ui,sans-serif;margin:32px;color:#172436;background:#f3f5f7}}
main{{max-width:1200px;margin:auto}}section{{background:white;padding:24px;margin:16px 0;border-radius:12px}}
.run{{display:flex;gap:32px;flex-wrap:wrap}}img{{image-rendering:pixelated;border:1px solid #ccd3dc;max-width:100%;height:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid #dce1e8;padding:8px;text-align:left;vertical-align:top}}
pre{{white-space:pre-wrap;margin:0;overflow-wrap:anywhere}}code{{overflow-wrap:anywhere}}.scroll{{overflow:auto}}p{{line-height:1.5}}</style>
<main><h1>Explay B220: выполнение оригинальной прошивки</h1>
<section class="run"><div>{screen}</div><div><h2>Состояние запуска</h2>
<p>Остановка: <strong>{h(result['stop']['reason'])}</strong><br>PC: <code>{h(result['stop'].get('pc',''))}</code><br>
Время на компьютере: {result['elapsed_seconds']} с<br>Нормализованный ID SoC: {h(result['normalized_chip'])}</p>
<p>Режим: {"explore — неизвестные MMIO разрешены как нули/память" if result['explore'] else "strict — остановка на неизвестном MMIO"}.<br>
Обращений к неизвестным MMIO: {result['unknown_mmio_accesses']}.</p>
<p>Это исследовательская модель платы. Показанный кадр записала прошивка через виртуальные LCD/DMA.
Работают загрузочный протокол DSP, базовая доставка IRQ, таймеры, RTC и часть JPEG-декодера.
Сам DSP-код, радиотракт и кнопки пока не реализованы. Получены экраны интерфейса MMI.</p>
<p>PS SHA-256: <code>{h(result['image_sha256'])}</code></p></div></section>
<section><h2>Передачи в LCD</h2><ul>{transfers}</ul>
<p>Исходные файлы прошивки не изменяются. {nor_state}</p></section>
<section><h2>Отладочные сообщения прошивки</h2><p>Формат и первые три аргумента AAPCS сохранены отдельно; аргументы со стека здесь не декодируются.</p>
<div class="scroll"><table><thead><tr><th>№</th><th>LR</th><th>Формат</th><th>R1–R3</th></tr></thead><tbody>{logs}</tbody></table></div></section></main></html>'''
    path.write_text(text,encoding='utf-8')
