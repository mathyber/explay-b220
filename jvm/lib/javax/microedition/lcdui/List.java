package javax.microedition.lcdui;

import java.util.Vector;
import b22.Events;

/* Full-screen choice list: up/down move the focus, OK selects. */
public class List extends Screen implements Choice {
    public static final Command SELECT_COMMAND = new Command("", Command.SCREEN, 0);
    private static final int ROW = 20;

    private final int type;
    private final Vector strings = new Vector(), images = new Vector(), fonts = new Vector();
    private boolean[] selected = new boolean[4];
    private int focus, top, fit;
    private Command selectCommand = SELECT_COMMAND;

    public List(String title, int listType) {
        this(title, listType, new String[0], null);
    }

    public List(String title, int listType, String[] stringElements, Image[] imageElements) {
        if (listType != EXCLUSIVE && listType != MULTIPLE && listType != IMPLICIT) throw new IllegalArgumentException();
        if (stringElements == null) throw new NullPointerException();
        if (imageElements != null && imageElements.length != stringElements.length) throw new IllegalArgumentException();
        this.title = title;
        this.type = listType;
        for (int i = 0; i < stringElements.length; i++) append(stringElements[i], imageElements == null ? null : imageElements[i]);
    }

    private void check(int n, int limit) {
        if (n < 0 || n >= limit) throw new IndexOutOfBoundsException();
    }

    public int size() { return strings.size(); }
    public String getString(int n) { check(n, size()); return (String) strings.elementAt(n); }
    public Image getImage(int n) { check(n, size()); return (Image) images.elementAt(n); }

    public int append(String s, Image image) {
        insert(size(), s, image);
        return size() - 1;
    }

    public void insert(int n, String s, Image image) {
        if (s == null) throw new NullPointerException();
        check(n, size() + 1);
        strings.insertElementAt(s, n);
        images.insertElementAt(image, n);
        fonts.insertElementAt(null, n);
        boolean[] next = new boolean[Math.max(selected.length, size())];
        System.arraycopy(selected, 0, next, 0, n);
        System.arraycopy(selected, n, next, n + 1, size() - 1 - n);
        selected = next;
        if (type != MULTIPLE && size() == 1) selected[0] = true;
        b22Changed();
    }

    public void delete(int n) {
        check(n, size());
        boolean was = selected[n];
        strings.removeElementAt(n);
        images.removeElementAt(n);
        fonts.removeElementAt(n);
        System.arraycopy(selected, n + 1, selected, n, selected.length - n - 1);
        selected[selected.length - 1] = false;
        if (focus >= size()) focus = Math.max(0, size() - 1);
        if (was && type != MULTIPLE && size() > 0) selected[Math.min(n, size() - 1)] = true;
        b22Changed();
    }

    public void deleteAll() {
        strings.removeAllElements();
        images.removeAllElements();
        fonts.removeAllElements();
        selected = new boolean[4];
        focus = top = 0;
        b22Changed();
    }

    public void set(int n, String s, Image image) {
        check(n, size());
        if (s == null) throw new NullPointerException();
        strings.setElementAt(s, n);
        images.setElementAt(image, n);
        b22Changed();
    }

    public boolean isSelected(int n) { check(n, size()); return selected[n]; }

    public int getSelectedIndex() {
        if (type == MULTIPLE) return -1;
        for (int i = 0; i < size(); i++) if (selected[i]) return i;
        return -1;
    }

    public int getSelectedFlags(boolean[] flags) {
        if (flags.length < size()) throw new IllegalArgumentException();
        int count = 0;
        for (int i = 0; i < flags.length; i++) {
            flags[i] = i < size() && selected[i];
            if (flags[i]) count++;
        }
        return count;
    }

    public void setSelectedIndex(int n, boolean on) {
        check(n, size());
        if (type == MULTIPLE) selected[n] = on;
        else if (on) {
            for (int i = 0; i < size(); i++) selected[i] = i == n;
            focus = n;
        }
        b22Changed();
    }

    public void setSelectedFlags(boolean[] flags) {
        if (flags.length < size()) throw new IllegalArgumentException();
        if (type == MULTIPLE) {
            for (int i = 0; i < size(); i++) selected[i] = flags[i];
        } else {
            int first = 0;
            while (first < size() && !flags[first]) first++;
            if (size() > 0) setSelectedIndex(first < size() ? first : 0, true);
        }
        b22Changed();
    }

    public void setFitPolicy(int policy) { fit = policy; }
    public int getFitPolicy() { return fit; }
    public void setFont(int n, Font font) { check(n, size()); fonts.setElementAt(font, n); b22Changed(); }
    public Font getFont(int n) {
        check(n, size());
        Font f = (Font) fonts.elementAt(n);
        return f != null ? f : Font.getDefaultFont();
    }

    public void setSelectCommand(Command c) {
        if (type != IMPLICIT) return;
        if (selectCommand != SELECT_COMMAND) removeCommand(selectCommand);
        selectCommand = c == null ? SELECT_COMMAND : c;
        if (c != null && c != SELECT_COMMAND) addCommand(c);
    }

    public void removeCommand(Command c) {
        if (c == selectCommand && c != SELECT_COMMAND) selectCommand = SELECT_COMMAND;
        super.removeCommand(c);
    }

    public void b22Key(int kind, int code) {
        if (softKey(kind, code) || kind == Events.KEY_RELEASED || size() == 0) return;
        if (code == -1 || code == Canvas.KEY_NUM2) focus = (focus + size() - 1) % size();
        else if (code == -2 || code == Canvas.KEY_NUM8) focus = (focus + 1) % size();
        else if ((code == -5 || code == Canvas.KEY_NUM5) && kind == Events.KEY_PRESSED) {
            if (type == MULTIPLE) selected[focus] = !selected[focus];
            else setSelectedIndex(focus, true);
            if (type == IMPLICIT && listener != null) listener.commandAction(selectCommand, this);
        } else return;
        b22Changed();
    }

    public void b22Paint(Graphics g) {
        int y0 = paintTitle(g);
        int rows = Math.max(1, (getHeight() - y0) / ROW);
        if (focus < top) top = focus;
        if (focus >= top + rows) top = focus - rows + 1;
        for (int i = top; i < size() && i < top + rows; i++) {
            int y = y0 + (i - top) * ROW;
            g.setClip(0, y, Display.WIDTH, ROW);
            if (i == focus) {
                g.setColor(0x3366cc);
                g.fillRect(0, y, Display.WIDTH, ROW);
            }
            int x = 4;
            if (type != IMPLICIT) {
                g.setColor(i == focus ? 0xffffff : 0x000000);
                if (type == EXCLUSIVE) g.drawArc(x, y + 5, 9, 9, 0, 360);
                else g.drawRect(x, y + 5, 9, 9);
                if (selected[i]) g.fillRect(x + 3, y + 8, 4, 4);
                x += 14;
            }
            Image image = (Image) images.elementAt(i);
            if (image != null) {
                g.drawImage(image, x, y + ROW / 2, Graphics.LEFT | Graphics.VCENTER);
                x += image.getWidth() + 3;
            }
            g.setFont(getFont(i));
            g.setColor(i == focus ? 0xffffff : 0x000000);
            g.drawString(getString(i), x, y + ROW / 2 - getFont(i).getHeight() / 2, Graphics.TOP | Graphics.LEFT);
        }
        g.setClip(0, 0, Display.WIDTH, Display.HEIGHT);
        paintCommands(g);
    }
}
