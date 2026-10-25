import java.sql.*;

/**
 * Reads a HelioFITS Core index the way Studio's FITS library tab would: read-only, while HelioFITS Core writes.
 * See docs/reader-contract.md.
 */
public class ReadIndex {
    /** The highest reader_compat this reader understands. */
    static final int READER_COMPAT = 1;

    /** reader_compat from the index's meta table; 1 for an index written before the key existed. */
    static int readerCompat(Connection c) throws SQLException {
        try (Statement s = c.createStatement();
             ResultSet rs = s.executeQuery("SELECT value FROM meta WHERE key = 'reader_compat'")) {
            return rs.next() ? Integer.parseInt(rs.getString(1)) : 1;
        }
    }

    public static void main(String[] args) throws Exception {
        String url = "jdbc:sqlite:file:" + args[0] + "?mode=ro";
        int rounds = Integer.parseInt(args[1]);
        try (Connection c = DriverManager.getConnection(url)) {
            try (Statement s = c.createStatement()) { s.execute("PRAGMA busy_timeout = 5000"); }
            int compat = readerCompat(c);
            if (compat > READER_COMPAT) {
                System.out.println("index needs a newer reader (reader_compat " + compat + "); not reading it");
                System.exit(3);
            }
            int last = -1, changes = 0;
            for (int i = 0; i < rounds; i++) {
                try (Statement s = c.createStatement();
                     ResultSet rs = s.executeQuery("SELECT COUNT(*) FROM current_files")) {
                    rs.next();
                    int n = rs.getInt(1);
                    if (n < last) throw new IllegalStateException("count went backwards: " + last + " -> " + n);
                    if (n != last) changes++;
                    last = n;
                }
                Thread.sleep(10);
            }
            try (Statement s = c.createStatement();
                 ResultSet rs = s.executeQuery("SELECT label, COUNT(*) FROM current_files GROUP BY label")) {
                while (rs.next()) System.out.println(rs.getString(1) + "\t" + rs.getInt(2));
            }
            try (Statement s = c.createStatement()) {
                s.execute("DELETE FROM files");
                System.out.println("ERROR: write succeeded on a read-only connection");
                System.exit(2);
            } catch (SQLException expected) {
                System.out.println("write refused as expected");
            }
            System.out.println("final count " + last + ", distinct counts seen " + changes);
        }
    }
}
