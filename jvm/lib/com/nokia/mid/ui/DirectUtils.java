package com.nokia.mid.ui;

import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;

public class DirectUtils {
    private DirectUtils() {}

    public static DirectGraphics getDirectGraphics(Graphics g) {
        return new Direct(g);
    }

    public static Image createImage(int w, int h, int argb) {
        int[] rgb = new int[w * h];
        for (int i = 0; i < rgb.length; i++) rgb[i] = argb;
        return Image.createRGBImage(rgb, w, h, true);
    }

    public static Image createImage(byte[] data, int offset, int length) {
        return Image.createImage(data, offset, length);
    }
}
