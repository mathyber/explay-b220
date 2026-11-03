"""Icon studio: redraw firmware icons pixel by pixel and see their size in the firmware format.

Run as python -m tools.icon_studio (or "Start B220 ICONS.cmd"). Shows the images
of the current firmware by group; an edited icon is saved as
icons/<module>/<index>.png (RGBA, original size), which is all the firmware
build needs to know where it goes. icons/unused.txt lists icons marked as
probably unused. Sizes are computed with tools.image_codec, the encoder the
replacement uses.
"""
import argparse
from collections import Counter
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from emulator.firmware import ROOT
from .extract_images import FIRMWARE, GROUPS, catalog, decode
from .image_codec import DECODERS, encode

OUTPUT = ROOT/'icons'
GROUP_NAMES = {
    'status-bar': 'шапка (батарея, сигнал, значки)',
    'main-menu-45x40': 'главное меню 45×40',
    'submenu-29': 'подменю 29×29',
    'list-16': 'списки и «Опции» 16×16',
    'dialer-keys': 'кнопки набора номера',
    'dialogs': 'диалоги',
    'audio-player': 'аудиоплеер',
    'clock-digits': 'цифры часов',
}
FORMATS = {'0x1': 'MB: прозрачность только да/нет', '0x30c': 'ABMP: плавная прозрачность'}
EDITOR = 440   # editor canvas side, pixels
THUMB = 52
COLUMNS = 5


def checkerboard(width, height, cell):
    board = Image.new('RGBA', (width, height), (200, 200, 200, 255))
    draw = ImageDraw.Draw(board)
    for y in range(0, height, cell):
        for x in range((y//cell % 2)*cell, width, cell*2):
            draw.rectangle((x, y, x+cell-1, y+cell-1), fill=(235, 235, 235, 255))
    return board


def line(x0, y0, x1, y1):
    """Pixels of a Bresenham line."""
    dx, dy = abs(x1-x0), -abs(y1-y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    error = dx+dy
    while True:
        yield x0, y0
        if (x0, y0) == (x1, y1):
            return
        twice = 2*error
        if twice >= dy:
            error += dy
            x0 += sx
        if twice <= dx:
            error += dx
            y0 += sy


class Studio:
    def __init__(self, root, firmware, output):
        self.root, self.output = root, output
        self.rom = firmware.read_bytes()
        self.entries = [e for e in catalog(self.rom) if e['type'] in DECODERS]
        self.unused_path = output/'unused.txt'
        self.unused = set(self.unused_path.read_text(encoding='utf-8').split()) if self.unused_path.exists() else set()
        self.originals = {}
        self.current = None
        self.picture = None
        self.dirty = False
        self.undo = []
        self.color = (255, 255, 255, 255)
        self.tool = tk.StringVar(value='pencil')
        self.last = None
        self.size_job = None
        self.build()
        self.fill_groups()

    # ---------- layout ----------

    def build(self):
        self.root.title('B220: иконки')
        self.root.geometry('1180x760')
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill='x')
        ttk.Label(top, text='Группа:').pack(side='left')
        self.group = ttk.Combobox(top, state='readonly', width=46)
        self.group.pack(side='left', padx=6)
        self.group.bind('<<ComboboxSelected>>', lambda _: self.show_group())
        self.total = ttk.Label(top, text='')
        self.total.pack(side='right')

        body = ttk.Frame(self.root)
        body.pack(fill='both', expand=True)
        left = ttk.Frame(body)
        left.pack(side='left', fill='y')
        self.grid = tk.Canvas(left, width=COLUMNS*(THUMB+8)+4, bg='#3c3c46', highlightthickness=0)
        bar = ttk.Scrollbar(left, orient='vertical', command=self.grid.yview)
        self.grid.configure(yscrollcommand=bar.set)
        self.grid.pack(side='left', fill='y')
        bar.pack(side='left', fill='y')
        self.grid.bind('<MouseWheel>', lambda e: self.grid.yview_scroll(-e.delta//120, 'units'))

        right = ttk.Frame(body, padding=8)
        right.pack(side='left', fill='both', expand=True)
        self.title = ttk.Label(right, text='Выберите иконку слева', font=('Segoe UI', 11, 'bold'))
        self.title.pack(anchor='w')
        self.format = ttk.Label(right, text='')
        self.format.pack(anchor='w')
        work = ttk.Frame(right)
        work.pack(fill='both', expand=True, pady=6)
        self.canvas = tk.Canvas(work, width=EDITOR, height=EDITOR, bg='#2a2a30', highlightthickness=0)
        self.canvas.pack(side='left', anchor='n')
        self.canvas.bind('<Button-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', lambda _: setattr(self, 'last', None))

        side = ttk.Frame(work, padding=(10, 0))
        side.pack(side='left', fill='y')
        tools = ttk.LabelFrame(side, text='Инструмент', padding=4)
        tools.pack(fill='x')
        for value, text in (('pencil', 'Карандаш'), ('eraser', 'Ластик'), ('fill', 'Заливка'), ('picker', 'Пипетка')):
            ttk.Radiobutton(tools, text=text, value=value, variable=self.tool).pack(anchor='w')
        colour = ttk.LabelFrame(side, text='Цвет', padding=4)
        colour.pack(fill='x', pady=6)
        self.swatch = tk.Label(colour, width=6, relief='solid', bg='#ffffff', cursor='hand2')
        self.swatch.pack(anchor='w')
        self.swatch.bind('<Button-1>', lambda _: self.choose_colour())
        ttk.Label(colour, text='Непрозрачность').pack(anchor='w', pady=(6, 0))
        self.alpha = tk.Scale(colour, from_=0, to=255, orient='horizontal', length=150, command=self.set_alpha)
        self.alpha.set(255)
        self.alpha.pack(anchor='w')
        ttk.Label(colour, text='Цвета оригинала:').pack(anchor='w', pady=(6, 0))
        self.palette = ttk.Frame(colour)
        self.palette.pack(anchor='w')

        actions = ttk.LabelFrame(side, text='Действия', padding=4)
        actions.pack(fill='x')
        for text, command in (('Начать с оригинала', self.from_original), ('Пустой холст', self.blank),
                              ('Импорт PNG…', self.import_png), ('Отменить  Ctrl+Z', self.undo_step),
                              ('Сохранить  Ctrl+S', self.save), ('Удалить мою версию', self.delete)):
            ttk.Button(actions, text=text, command=command).pack(fill='x', pady=1)
        self.unused_var = tk.BooleanVar()
        ttk.Checkbutton(actions, text='Похоже, не используется', variable=self.unused_var,
                        command=self.toggle_unused).pack(anchor='w', pady=(6, 0))

        previews = ttk.LabelFrame(right, text='Как будет на телефоне: оригинал | новая, 1:1 и ×2, на чёрном и белом', padding=4)
        previews.pack(fill='x')
        self.preview = tk.Canvas(previews, height=120, bg='#808080', highlightthickness=0)
        self.preview.pack(fill='x')
        self.sizes = ttk.Label(right, text='', font=('Segoe UI', 10))
        self.sizes.pack(anchor='w', pady=4)
        self.root.bind('<Control-z>', lambda _: self.undo_step())
        self.root.bind('<Control-s>', lambda _: self.save())

    def fill_groups(self):
        self.groups = [(f'main: {GROUP_NAMES[g]}', lambda e, g=g: e.get('group') == g) for g in GROUP_NAMES]
        self.groups.append(('main: остальное', lambda e: e['module'] == 'main' and 'group' not in e))
        for module in dict.fromkeys(e['module'] for e in self.entries if e['module'] != 'main'):
            self.groups.append((module, lambda e, m=module: e['module'] == m))
        self.group['values'] = [name for name, _ in self.groups]
        self.group.current(0)
        self.show_group()
        self.update_total()

    # ---------- icon list ----------

    def key(self, entry):
        return f'{entry["module"]}/{entry["index"]}'

    def edited_path(self, entry):
        return self.output/entry['module']/f'{entry["index"]:03d}.png'

    def original(self, entry):
        k = self.key(entry)
        if k not in self.originals:
            self.originals[k] = decode(self.rom, entry)
        return self.originals[k]

    def show_group(self):
        _, test = self.groups[self.group.current()]
        self.visible = [e for e in self.entries if test(e)]
        self.grid.delete('all')
        self.thumbs = []
        for n, entry in enumerate(self.visible):
            x, y = 4+n % COLUMNS*(THUMB+8), 4+n//COLUMNS*(THUMB+20)
            path = self.edited_path(entry)
            picture = Image.open(path) if path.exists() else self.original(entry)
            thumb = picture.copy()
            scale = max(1, min(3, THUMB//max(thumb.size)))
            if scale > 1:
                thumb = thumb.resize((thumb.width*scale, thumb.height*scale), Image.NEAREST)
            thumb.thumbnail((THUMB, THUMB))
            photo = ImageTk.PhotoImage(thumb)
            self.thumbs.append(photo)
            unused = self.key(entry) in self.unused
            outline = '#3fd06a' if path.exists() else ('#a05050' if unused else '#55555f')
            tag = f'icon{n}'
            self.grid.create_rectangle(x, y, x+THUMB+4, y+THUMB+4, outline=outline, width=2,
                                       fill='#26262c', tags=tag)
            self.grid.create_image(x+2+THUMB//2, y+2+THUMB//2, image=photo, tags=tag)
            label = f'{entry["index"]}' + (' ✎' if path.exists() else '') + (' ✗' if unused else '')
            self.grid.create_text(x+THUMB//2+2, y+THUMB+12, text=label, fill='#e0e0e0', font=('Segoe UI', 8), tags=tag)
            self.grid.tag_bind(tag, '<Button-1>', lambda _, e=entry: self.select(e))
        rows = (len(self.visible)+COLUMNS-1)//COLUMNS
        self.grid.configure(scrollregion=(0, 0, COLUMNS*(THUMB+8), rows*(THUMB+20)+8))
        self.grid.yview_moveto(0)

    # ---------- editing ----------

    def select(self, entry):
        if not self.confirm_switch():
            return
        self.current = entry
        path = self.edited_path(entry)
        self.picture = Image.open(path).convert('RGBA') if path.exists() else self.original(entry).copy()
        self.undo, self.dirty = [], False
        self.zoom = max(1, min(24, EDITOR//max(entry['width'], entry['height'])))
        self.title['text'] = f'{entry["module"]}/{entry["index"]}  —  {entry["width"]}×{entry["height"]} px'
        self.format['text'] = FORMATS[entry['type']] + ('   (ваша версия)' if path.exists() else '')
        self.unused_var.set(self.key(entry) in self.unused)
        common = Counter(p for p in self.original(entry).getdata() if p[3]).most_common(16)
        for child in self.palette.winfo_children():
            child.destroy()
        for n, (colour, _) in enumerate(common):
            swatch = tk.Label(self.palette, width=2, bg='#%02x%02x%02x' % colour[:3], relief='solid', cursor='hand2')
            swatch.grid(row=n//8, column=n % 8, padx=1, pady=1)
            swatch.bind('<Button-1>', lambda _, c=colour: self.set_colour(c))
        self.redraw()

    def confirm_switch(self):
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel('Несохранённые изменения', f'Сохранить {self.key(self.current)}?')
        if answer is None:
            return False
        if answer:
            self.save()
        return True

    def remember(self):
        self.undo.append(self.picture.copy())
        del self.undo[:-50]
        self.dirty = True

    def cell(self, event):
        x, y = event.x//self.zoom, event.y//self.zoom
        if self.picture and 0 <= x < self.picture.width and 0 <= y < self.picture.height:
            return x, y
        return None

    def press(self, event):
        point = self.cell(event)
        if not point:
            return
        tool = self.tool.get()
        if tool == 'picker':
            self.set_colour(self.picture.getpixel(point))
            return
        self.remember()
        if tool == 'fill':
            self.flood(point)
        else:
            self.plot(point)
        self.last = point
        self.redraw()

    def drag(self, event):
        point = self.cell(event)
        if not point or self.tool.get() not in ('pencil', 'eraser') or self.last is None:
            return
        for p in line(*self.last, *point):
            self.plot(p)
        self.last = point
        self.redraw()

    def plot(self, point):
        self.picture.putpixel(point, (0, 0, 0, 0) if self.tool.get() == 'eraser' else self.color)

    def flood(self, start):
        target = self.picture.getpixel(start)
        if target == self.color:
            return
        stack, pixels = [start], self.picture.load()
        width, height = self.picture.size
        while stack:
            x, y = stack.pop()
            if pixels[x, y] != target:
                continue
            pixels[x, y] = self.color
            for nx, ny in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
                if 0 <= nx < width and 0 <= ny < height:
                    stack.append((nx, ny))

    def set_colour(self, colour):
        self.color = tuple(colour)
        self.swatch['bg'] = '#%02x%02x%02x' % self.color[:3]
        self.alpha.set(self.color[3])

    def set_alpha(self, value):
        self.color = (*self.color[:3], int(value))

    def choose_colour(self):
        chosen = colorchooser.askcolor(color='#%02x%02x%02x' % self.color[:3], title='Цвет')
        if chosen[0]:
            self.set_colour((*map(int, chosen[0]), self.color[3]))

    def from_original(self):
        if self.current:
            self.remember()
            self.picture = self.original(self.current).copy()
            self.redraw()

    def blank(self):
        if self.current:
            self.remember()
            self.picture = Image.new('RGBA', self.picture.size, (0, 0, 0, 0))
            self.redraw()

    def import_png(self):
        if not self.current:
            return
        name = filedialog.askopenfilename(filetypes=[('Картинки', '*.png *.gif *.bmp *.jpg'), ('Все файлы', '*.*')])
        if not name:
            return
        source = Image.open(name).convert('RGBA')
        width, height = self.picture.size
        if source.size != (width, height):
            # Fit inside, keeping proportions; pixel art grows by whole steps.
            scale = min(width/source.width, height/source.height)
            size = (max(1, round(source.width*scale)), max(1, round(source.height*scale)))
            whole = scale >= 1 and scale == int(scale)
            source = source.resize(size, Image.NEAREST if whole else Image.LANCZOS)
            canvas = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            canvas.paste(source, ((width-size[0])//2, (height-size[1])//2))
            source = canvas
        self.remember()
        self.picture = source
        self.redraw()

    def undo_step(self):
        if self.undo:
            self.picture = self.undo.pop()
            self.dirty = True
            self.redraw()

    def save(self):
        if not self.current:
            return
        path = self.edited_path(self.current)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.picture.save(path)
        self.dirty = False
        self.format['text'] = FORMATS[self.current['type']] + '   (ваша версия)'
        self.show_group_keep()
        self.update_total()

    def delete(self):
        if not self.current:
            return
        path = self.edited_path(self.current)
        if path.exists() and messagebox.askyesno('Удалить', f'Удалить вашу версию {self.key(self.current)}?'):
            path.unlink()
            self.picture = self.original(self.current).copy()
            self.dirty = False
            self.format['text'] = FORMATS[self.current['type']]
            self.redraw()
            self.show_group_keep()
            self.update_total()

    def toggle_unused(self):
        if not self.current:
            return
        (self.unused.add if self.unused_var.get() else self.unused.discard)(self.key(self.current))
        self.output.mkdir(parents=True, exist_ok=True)
        self.unused_path.write_text(''.join(f'{k}\n' for k in sorted(self.unused)), encoding='utf-8')
        self.show_group_keep()

    def show_group_keep(self):
        position = self.grid.yview()[0]
        self.show_group()
        self.grid.yview_moveto(position)

    # ---------- drawing ----------

    def redraw(self):
        width, height = self.picture.size
        z = self.zoom
        view = checkerboard(width*z, height*z, max(4, z//2))
        view.alpha_composite(self.picture.resize((width*z, height*z), Image.NEAREST))
        if z >= 6:
            draw = ImageDraw.Draw(view)
            for x in range(0, width*z+1, z):
                draw.line((x, 0, x, height*z), fill=(120, 120, 140, 255))
            for y in range(0, height*z+1, z):
                draw.line((0, y, width*z, y), fill=(120, 120, 140, 255))
        self.editor_photo = ImageTk.PhotoImage(view)
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, anchor='nw', image=self.editor_photo)
        if self.size_job:
            self.root.after_cancel(self.size_job)
        self.size_job = self.root.after(200, self.update_preview)

    def phone_look(self, picture):
        """What the phone shows: encoded in the slot's format and decoded back."""
        data = encode(picture, self.current['type'])
        return DECODERS[self.current['type']](data), len(data)

    def update_preview(self):
        self.size_job = None
        original = self.original(self.current)
        new, size = self.phone_look(self.picture)
        old = self.current['size']
        self.preview.delete('all')
        self.preview_photos = []
        x = 6
        for background in ((0, 0, 0, 255), (255, 255, 255, 255)):
            for picture in (original, new):
                for scale in (1, 2):
                    shown = picture.resize((picture.width*scale, picture.height*scale), Image.NEAREST)
                    if shown.height > 108:
                        shown.thumbnail((400, 108))
                    tile = Image.new('RGBA', (shown.width+8, shown.height+8), background)
                    tile.alpha_composite(shown, (4, 4))
                    photo = ImageTk.PhotoImage(tile)
                    self.preview_photos.append(photo)
                    self.preview.create_image(x, 6, anchor='nw', image=photo)
                    x += tile.width+6
                x += 10
        delta = size-old
        colour = '#1a7f37' if delta <= 0 else '#c0392b'
        self.sizes.configure(text=f'Вес в прошивке: оригинал {old} Б  →  новая {size} Б  ({delta:+d} Б)'
                                  + ('' if delta <= 0 else '   — тяжелее оригинала, понадобится место из свободного'),
                             foreground=colour)

    def update_total(self):
        count, delta = 0, 0
        for entry in self.entries:
            path = self.edited_path(entry)
            if path.exists():
                count += 1
                delta += len(encode(Image.open(path), entry['type']))-entry['size']
        self.total['text'] = f'Ваших иконок: {count}, вес относительно оригиналов: {delta:+d} Б'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, default=FIRMWARE)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if not args.firmware.exists():
        parser.error(f'{args.firmware} not found: build the firmware first (Start B220 CUT.cmd)')
    root = tk.Tk()
    Studio(root, args.firmware, args.output)
    root.mainloop()


if __name__ == '__main__':
    main()
