import os

import psycopg


def main():
    try:
        with psycopg.connect(os.environ["TEST_DATABASE_URL"], connect_timeout=5) as connection:
            connection.execute("SELECT 1")
    except psycopg.Error as error:
        message = str(error).lower()
        if "ident authentication failed" in message or "no pg_hba.conf entry" in message:
            print("Local PostgreSQL authentication needs an app-specific password rule")
            raise SystemExit(42) from None
        print("PostgreSQL connection failed; check the configured password, server status and database")
        raise SystemExit(1) from None
    print("Local PostgreSQL connection verified")


if __name__ == "__main__":
    main()
