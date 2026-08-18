import datetime
import logging
import os
import shutil

DB_PATH = 'db.sqlite3'
BACKUP_DIR = 'backups'
RETENTION_DAYS = int(os.getenv('BACKUP_RETENTION_DAYS', '2'))
logger = logging.getLogger('lastfm_bot')


def run_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)

    today = datetime.date.today().isoformat()
    backup_name = f"db-{today}.sqlite3"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    shutil.copy2(DB_PATH, backup_path)
    logger.info('Backup created: %s', backup_path)

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=RETENTION_DAYS)
    for fname in os.listdir(BACKUP_DIR):
        fpath = os.path.join(BACKUP_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath), tz=datetime.timezone.utc)
        if mtime < cutoff:
            os.remove(fpath)
            logger.info('Deleted old backup: %s', fname)


if __name__ == '__main__':
    run_backup()
