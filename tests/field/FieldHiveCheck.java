import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.Locale;

class FieldHiveCheck {
    public static void main(String[] args) {
        int stage = 1;
        try {
            Class.forName("org.apache.hive.jdbc.HiveDriver");
            stage = 2;
            System.out.println("connect");
            try (Connection db = DriverManager.getConnection(System.getenv("DATAMIND_FIELD_DSN"),
                    System.getenv("DATAMIND_FIELD_USER"), System.getenv("DATAMIND_FIELD_PASSWORD"));
                 Statement statement = db.createStatement()) {
                stage = 3;
                System.out.println("identity");
                // HiveServer2 endpoints: Apache Hive, or the Spark Thrift Server ("Spark SQL"), as in hive_bridge.ENGINES
                String product = db.getMetaData().getDatabaseProductName().toLowerCase(Locale.ROOT);
                if (!product.contains("hive") && !product.contains("spark sql")) {
                    System.exit(stage);
                }
                stage = 4;
                System.out.println("aggregate");
                try (ResultSet rows = statement.executeQuery(args[0])) {
                    if (!rows.next() || rows.getLong(1) != 33 || rows.getObject(2) != null || rows.next()) {
                        System.exit(stage);
                    }
                }
                stage = 5;
                System.out.println("tables");
                try (ResultSet tables = statement.executeQuery("SHOW TABLES")) {
                    tables.next();  // An empty schema is a valid catalog.
                }
            }
        } catch (Exception failure) {
            System.exit(stage);
        }
    }
}
