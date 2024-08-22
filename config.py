import configparser

CONFIG_FILE = "config.ini"
CONFIG = configparser.ConfigParser()
CONFIG.read(CONFIG_FILE)

SECRETS_FILE = "secrets.ini"
SECRETS = configparser.ConfigParser()
SECRETS.read(SECRETS_FILE)