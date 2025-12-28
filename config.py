import configparser

CONFIG_FILE = "config.ini"
CONFIG = configparser.ConfigParser()
CONFIG.read(CONFIG_FILE, encoding="utf-8")

SECRETS_FILE = "secrets.ini"
SECRETS = configparser.ConfigParser()
SECRETS.read(SECRETS_FILE)