package javax.microedition.lcdui;

/* Accepted but not shown. */
public class Ticker {
    private String text;

    public Ticker(String text) {
        setString(text);
    }

    public String getString() { return text; }

    public void setString(String text) {
        if (text == null) throw new NullPointerException();
        this.text = text;
    }
}
