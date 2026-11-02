package org.helioviewer.jhv.view.uri;

import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.net.URI;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Optional;

import org.helioviewer.jhv.app.AppInit;
import org.helioviewer.jhv.app.DisplaySettings;
import org.helioviewer.jhv.app.Log;
import org.helioviewer.jhv.app.Platform;
import org.helioviewer.jhv.io.Directories;
import org.helioviewer.jhv.io.FitsHeaderContainer;
import org.helioviewer.jhv.io.LascoBackground;
import org.helioviewer.jhv.math.Vec2;
import org.helioviewer.jhv.metadata.FitsMetaData;
import org.helioviewer.jhv.metadata.Region;
import org.helioviewer.jhv.wcs.WcsProjection;
import org.json.JSONArray;
import org.json.JSONObject;

import nom.tam.fits.BasicHDU;
import nom.tam.fits.Fits;
import nom.tam.fits.FitsFactory;
import nom.tam.fits.Header;
import nom.tam.image.compression.hdu.CompressedImageHDU;

/**
 * Writes a decode dump (format hf-decode-dump/1, HelioFITS Core docs/decode-parity.md): the numbers Studio's
 * decoder produces for one frame, before any colour table, clip or stretch, the observation time it
 * reads, and where a grid of pixels lands on the sky, in the form the sunpy parity test compares.
 *
 * <p>Not a check (no "Check" suffix, so run-checks.sh leaves it alone): a utility that wants real
 * files and, for the LASCO background, the network. It lives in this package because
 * {@link FITSImage#readData} is package-private and is the decoder itself; calling it, rather than
 * re-deriving its steps, is the point.
 *
 * <p>Run: java -Djava.awt.headless=true -cp "bin:extra/test-classes:resources:lib/*" org.helioviewer.jhv.view.uri.DecodeParityDump
 * OUT_DIR FILE.fits [--plane N] [--subtract-background]
 *
 * <p>Without --subtract-background a LASCO frame is dumped as served (DN), which is what the layer shows
 * with its Background switch off. With it the dump carries the per-second frame and the NRL background
 * array that was subtracted, so the comparison tests the arithmetic against the same background.
 * Settings are read from DisplaySettings and written into the dump; the comparison refuses a dump
 * taken with the radius or AIA normalisation on, or a time mode other than Observer.
 */
public final class DecodeParityDump {

    private static final int BAD_PIXEL = Integer.MIN_VALUE;
    private static final int GRID = 9;

    private DecodeParityDump() {}

    public static void main(String[] args) throws Exception {
        if (args.length < 2)
            throw new IllegalArgumentException("usage: DecodeParityDump OUT_DIR FILE.fits [--plane N] [--subtract-background]");
        Path outDir = Path.of(args[0]);
        File file = new File(args[1]);
        int plane = 0;
        boolean subtract = false;
        for (int i = 2; i < args.length; i++) {
            if (args[i].equals("--plane"))
                plane = Integer.parseInt(args[++i]);
            else if (args[i].equals("--subtract-background"))
                subtract = true;
            else
                throw new IllegalArgumentException("unknown option " + args[i]);
        }

        if (System.getProperty("user.timezone") == null)
            System.setProperty("user.timezone", "UTC");
        FitsFactory.setUseHierarch(true);
        FitsFactory.setLongStringsEnabled(true);
        // Building FitsMetaData initialises Sun, which asks SPICE for Earth's position (as LascoRealFrameDump).
        System.setProperty("user.home", Files.createTempDirectory("hfs-decode-dump").toString());
        Platform.init();
        Directories.createCacheDirs();
        AppInit.loadSpice();
        // Studio ships with this on (Settings defaults); the parity claim is made with it off. It only scales the
        // display, but the dump records the setting, so set it for real in the throwaway home.
        DisplaySettings.setNormalizeAIA(false);

        Files.createDirectories(outDir);
        write(outDir, file, plane, subtract);
    }

    static void write(Path outDir, File file, int plane, boolean subtract) throws Exception {
        LascoBackground.Choice choice = LascoBackground.Choice.DEFAULT;
        FITSData data = FITSImage.readData(file, plane, subtract, choice);
        Header header = data.header();
        int w = data.width(), h = data.height();

        // 1. the numbers
        int planes = 1;
        for (int axis = 3, n = header.getIntValue("NAXIS", 0); axis <= n; axis++)
            planes *= Math.max(1, header.getIntValue("NAXIS" + axis, 1));
        boolean planeOutOfRange = plane < 0 || plane >= planes;
        float[] values = physical(data);

        // 2. the corrections Studio applied
        JSONArray corrections = new JSONArray();
        JSONArray notes = new JSONArray();
        float[] background = null;
        String unit = header.getStringValue("BUNIT") == null ? "" : header.getStringValue("BUNIT").trim();
        if (data.background().subtracted()) {
            LascoBackground.Request request = data.background().request();
            LascoBackground.Result result = request.resolve(choice);
            background = result.perSecond();
            corrections.put(new JSONObject().put("name", "lasco_per_second").put("applied", true)
                    .put("params", new JSONObject().put("offset", header.getDoubleValue("OFFSET", 0))
                            .put("exptime", header.getDoubleValue("EXPTIME", 0))));
            unit = "DN/s";
        } else if (data.background().request() != null) {
            notes.put("LASCO frame dumped as served: the background was not subtracted (--subtract-background not given, or unavailable)");
        }
        if (planeOutOfRange)
            corrections.put(new JSONObject().put("name", "plane_out_of_range_is_zero").put("applied", true)
                    .put("params", new JSONObject().put("asked", plane)));
        if (data.pixels() instanceof byte[])
            notes.put("8-bit image: Studio shows the raw bytes and leaves BZERO and BSCALE unapplied; the values here are the raw bytes");
        if (data.provisional())
            notes.put("Studio marked this decode provisional (a correction it should have had was unavailable)");

        // 3. the time, by the same function Studio's layers use
        FitsHeaderContainer container = new FitsHeaderContainer(header);
        FitsMetaData.Observation obs = FitsMetaData.observation(container);
        String keyword = "DATE-OBS";
        for (String k : new String[]{"DATE-AVG", "DATE_AVG", "DATE_OBS", "DATE-OBS"}) {
            if (container.getString(k).isPresent()) {
                keyword = k;
                break;
            }
        }
        if (!keyword.equals("DATE-OBS"))
            corrections.put(new JSONObject().put("name", "time_prefers_date_avg").put("applied", true)
                    .put("params", new JSONObject().put("keyword", keyword)));

        // 4. the sky: the same path ImageBounds.hpc uses, on a grid of pixel centres
        FitsMetaData meta = new FitsMetaData(container, file.toURI());
        Region region = meta.getPhysicalRegion();
        JSONArray gridPixels = new JSONArray();
        JSONArray gridArcsec = new JSONArray();
        for (int j : steps(h)) {
            for (int i : steps(w)) {
                double x = region.llx + (i + 0.5) * region.width / w;
                double y = region.lly + (j + 0.5) * region.height / h;
                Vec2 hp = WcsProjection.planeToHelioprojective(meta.getWcsHeader(), x, y);
                gridPixels.put(new JSONArray().put((double) i).put((double) j));
                gridArcsec.put(new JSONArray().put(Math.toDegrees(hp.x) * 3600).put(Math.toDegrees(hp.y) * 3600));
            }
        }

        String stem = file.getName() + ".p" + plane;
        String base = stem + ".hfdump";
        npy(outDir.resolve(base + ".npy"), values, h, w);
        JSONObject dataSpec = new JSONObject().put("file", base + ".npy").put("encoding", "npy-float32-le");
        JSONObject doc = new JSONObject()
                .put("format", "hf-decode-dump/1")
                .put("writer", new JSONObject().put("name", "HelioFITS Studio DecodeParityDump").put("jvm", System.getProperty("java.version")))
                .put("source", new JSONObject().put("name", file.getName()).put("sha256", sha256(file)).put("size", file.length()))
                .put("hdu", hduIndex(file))
                .put("plane", planeOutOfRange ? 0 : plane)
                .put("instrument", family(header))
                .put("shape", new JSONArray().put(h).put(w))
                .put("orientation", "fits")
                .put("bad_pixels", "nan")
                .put("unit", unit)
                .put("settings", new JSONObject()
                        .put("normalize_radius", DisplaySettings.getNormalizeRadius())
                        .put("normalize_aia", DisplaySettings.getNormalizeAIA())
                        .put("time_mode", DisplaySettings.getTimeMode().name().toLowerCase())
                        .put("filter", "none"))
                .put("corrections", corrections)
                .put("time", new JSONObject().put("keyword", keyword).put("iso", Instant.ofEpochMilli(obs.time().milli).toString()))
                .put("wcs", new JSONObject().put("grid_pixels", gridPixels).put("grid_arcsec", gridArcsec))
                .put("data", dataSpec)
                .put("notes", notes);
        if (background != null) {
            npy(outDir.resolve(stem + ".bg.hfdump.npy"), background, h, w);
            doc.put("background", new JSONObject().put("file", stem + ".bg.hfdump.npy").put("encoding", "npy-float32-le"));
        }
        Files.writeString(outDir.resolve(base + ".json"), doc.toString(1));
        Log.info("decode dump " + outDir.resolve(base + ".json"));
    }

    /** Evenly spaced zero-based indices, ends included, as np.unique(np.round(np.linspace(0, n - 1, GRID))) gives them. */
    private static int[] steps(int n) {
        return java.util.stream.IntStream.range(0, GRID)
                .map(k -> (int) Math.rint(k * (n - 1) / (double) (GRID - 1))).distinct().toArray();
    }

    /** The decoded values in FITS row order: BZERO and BSCALE applied, blank and NaN as NaN. */
    static float[] physical(FITSData d) {
        int n = d.width() * d.height();
        float[] out = new float[n];
        for (int i = 0; i < n; i++) {
            out[i] = switch (d.pixels()) {
                case byte[] p -> p[i] & 0xFF;
                case short[] p -> d.hasBlank() && p[i] == (short) d.blank() ? Float.NaN : (float) (d.bzero() + p[i] * d.bscale());
                case int[] p -> d.hasBlank() && p[i] == (int) d.blank() ? Float.NaN : (float) (d.bzero() + p[i] * d.bscale());
                case long[] p -> d.hasBlank() && p[i] == d.blank() ? Float.NaN : (float) (d.bzero() + p[i] * d.bscale());
                case float[] p -> fromFloat(p[i], d.bzero(), d.bscale());
                case double[] p -> fromFloat(p[i], d.bzero(), d.bscale());
                default -> throw new IllegalStateException("pixel type " + d.pixels().getClass());
            };
        }
        return out;
    }

    private static float fromFloat(double v, double bzero, double bscale) {
        if (Double.isNaN(v))
            return Float.NaN;
        float f = (float) (bzero + v * bscale);
        return f == BAD_PIXEL ? Float.NaN : f; // the per-second path writes BAD_PIXEL for blank pixels
    }

    private static String family(Header header) {
        String instrument = str(header, "INSTRUME");
        int underscore = instrument.indexOf('_');
        if (underscore != -1)
            instrument = instrument.substring(0, underscore);
        if (instrument.equals("AIA") || instrument.equals("LASCO"))
            return instrument;
        if (str(header, "OBSRVTRY").equals("PUNCH") || str(header, "TELESCOP").toUpperCase().contains("PUNCH"))
            return "PUNCH";
        if (instrument.toUpperCase().contains("ASPIICS") || str(header, "DETECTOR").toUpperCase().contains("ASPIICS"))
            return "ASPIICS";
        if (instrument.startsWith("GOES-R Series Solar Ultraviolet Imager") || instrument.equals("SUVI"))
            return "SUVI";
        return instrument.isEmpty() ? "unknown" : instrument;
    }

    private static String str(Header header, String key) {
        String v = header.getStringValue(key);
        return v == null ? "" : v.trim();
    }

    /** FITSImage.findHDU's choice, as an index: the first compressed image, else the first image HDU with data. */
    private static int hduIndex(File file) throws Exception {
        try (Fits f = new Fits(file)) {
            BasicHDU<?>[] hdus = f.read();
            for (int i = 0; i < hdus.length; i++)
                if (hdus[i] instanceof CompressedImageHDU)
                    return i;
            for (int i = 0; i < hdus.length; i++)
                if (hdus[i] instanceof nom.tam.fits.ImageHDU ihdu && ihdu.getAxes() != null)
                    return i;
        }
        return 0;
    }

    private static String sha256(File file) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        try (var in = Files.newInputStream(file.toPath())) {
            byte[] buf = new byte[1 << 20];
            for (int n; (n = in.read(buf)) > 0; )
                md.update(buf, 0, n);
        }
        StringBuilder sb = new StringBuilder();
        for (byte b : md.digest())
            sb.append(String.format("%02x", b));
        return sb.toString();
    }

    /** NumPy .npy version 1.0, little-endian float32, C order, shape (h, w). */
    private static void npy(Path path, float[] values, int h, int w) throws IOException {
        String dict = "{'descr': '<f4', 'fortran_order': False, 'shape': (" + h + ", " + w + "), }";
        int pad = 64 - ((10 + dict.length() + 1) % 64);
        StringBuilder header = new StringBuilder(dict);
        header.append(" ".repeat(pad % 64)).append('\n');
        byte[] hb = header.toString().getBytes(StandardCharsets.US_ASCII);
        ByteBuffer buf = ByteBuffer.allocate(10 + hb.length + values.length * 4).order(ByteOrder.LITTLE_ENDIAN);
        buf.put((byte) 0x93).put("NUMPY".getBytes(StandardCharsets.US_ASCII)).put((byte) 1).put((byte) 0);
        buf.putShort((short) hb.length).put(hb);
        buf.asFloatBuffer().put(values);
        try (OutputStream out = Files.newOutputStream(path)) {
            out.write(buf.array());
        }
    }
}
