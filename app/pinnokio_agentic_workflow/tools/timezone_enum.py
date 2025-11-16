"""
Enum des timezones IANA principales par pays pour la détermination automatique.
"""

from enum import Enum
from typing import Dict, List


class TimezoneIANA(str, Enum):
    """
    Enum des timezones IANA principales organisées par pays.
    Format: PAYS_REGION = "Continent/Ville"
    """
    
    # EUROPE
    SWITZERLAND = "Europe/Zurich"
    FRANCE = "Europe/Paris"
    GERMANY = "Europe/Berlin"
    UNITED_KINGDOM = "Europe/London"
    ITALY = "Europe/Rome"
    SPAIN = "Europe/Madrid"
    PORTUGAL = "Europe/Lisbon"
    BELGIUM = "Europe/Brussels"
    NETHERLANDS = "Europe/Amsterdam"
    AUSTRIA = "Europe/Vienna"
    SWEDEN = "Europe/Stockholm"
    NORWAY = "Europe/Oslo"
    DENMARK = "Europe/Copenhagen"
    FINLAND = "Europe/Helsinki"
    POLAND = "Europe/Warsaw"
    CZECH_REPUBLIC = "Europe/Prague"
    GREECE = "Europe/Athens"
    IRELAND = "Europe/Dublin"
    LUXEMBOURG = "Europe/Luxembourg"
    
    # AMÉRIQUE DU NORD
    USA_EASTERN = "America/New_York"
    USA_CENTRAL = "America/Chicago"
    USA_MOUNTAIN = "America/Denver"
    USA_PACIFIC = "America/Los_Angeles"
    CANADA_EASTERN = "America/Toronto"
    CANADA_CENTRAL = "America/Winnipeg"
    CANADA_MOUNTAIN = "America/Edmonton"
    CANADA_PACIFIC = "America/Vancouver"
    MEXICO = "America/Mexico_City"
    
    # AMÉRIQUE DU SUD
    BRAZIL_SAO_PAULO = "America/Sao_Paulo"
    BRAZIL_MANAUS = "America/Manaus"
    ARGENTINA = "America/Argentina/Buenos_Aires"
    CHILE = "America/Santiago"
    COLOMBIA = "America/Bogota"
    PERU = "America/Lima"
    VENEZUELA = "America/Caracas"
    
    # ASIE
    CHINA = "Asia/Shanghai"
    JAPAN = "Asia/Tokyo"
    SOUTH_KOREA = "Asia/Seoul"
    INDIA = "Asia/Kolkata"
    SINGAPORE = "Asia/Singapore"
    HONG_KONG = "Asia/Hong_Kong"
    TAIWAN = "Asia/Taipei"
    THAILAND = "Asia/Bangkok"
    VIETNAM = "Asia/Ho_Chi_Minh"
    MALAYSIA = "Asia/Kuala_Lumpur"
    PHILIPPINES = "Asia/Manila"
    INDONESIA_JAKARTA = "Asia/Jakarta"
    PAKISTAN = "Asia/Karachi"
    BANGLADESH = "Asia/Dhaka"
    UAE = "Asia/Dubai"
    SAUDI_ARABIA = "Asia/Riyadh"
    ISRAEL = "Asia/Jerusalem"
    TURKEY = "Europe/Istanbul"
    
    # OCÉANIE
    AUSTRALIA_SYDNEY = "Australia/Sydney"
    AUSTRALIA_MELBOURNE = "Australia/Melbourne"
    AUSTRALIA_PERTH = "Australia/Perth"
    NEW_ZEALAND = "Pacific/Auckland"
    
    # AFRIQUE
    SOUTH_AFRICA = "Africa/Johannesburg"
    EGYPT = "Africa/Cairo"
    MOROCCO = "Africa/Casablanca"
    KENYA = "Africa/Nairobi"
    NIGERIA = "Africa/Lagos"
    
    # MOYEN-ORIENT
    LEBANON = "Asia/Beirut"
    JORDAN = "Asia/Amman"
    KUWAIT = "Asia/Kuwait"
    QATAR = "Asia/Qatar"


# Mapping pays (nom complet) -> Timezone
COUNTRY_TO_TIMEZONE: Dict[str, TimezoneIANA] = {
    # Europe
    "suisse": TimezoneIANA.SWITZERLAND,
    "switzerland": TimezoneIANA.SWITZERLAND,
    "schweiz": TimezoneIANA.SWITZERLAND,
    "svizzera": TimezoneIANA.SWITZERLAND,
    
    "france": TimezoneIANA.FRANCE,
    "frankreich": TimezoneIANA.FRANCE,
    "francia": TimezoneIANA.FRANCE,
    
    "allemagne": TimezoneIANA.GERMANY,
    "germany": TimezoneIANA.GERMANY,
    "deutschland": TimezoneIANA.GERMANY,
    
    "royaume-uni": TimezoneIANA.UNITED_KINGDOM,
    "united kingdom": TimezoneIANA.UNITED_KINGDOM,
    "uk": TimezoneIANA.UNITED_KINGDOM,
    "angleterre": TimezoneIANA.UNITED_KINGDOM,
    "england": TimezoneIANA.UNITED_KINGDOM,
    
    "italie": TimezoneIANA.ITALY,
    "italy": TimezoneIANA.ITALY,
    "italia": TimezoneIANA.ITALY,
    
    "espagne": TimezoneIANA.SPAIN,
    "spain": TimezoneIANA.SPAIN,
    "españa": TimezoneIANA.SPAIN,
    
    "portugal": TimezoneIANA.PORTUGAL,
    
    "belgique": TimezoneIANA.BELGIUM,
    "belgium": TimezoneIANA.BELGIUM,
    "belgië": TimezoneIANA.BELGIUM,
    
    "pays-bas": TimezoneIANA.NETHERLANDS,
    "netherlands": TimezoneIANA.NETHERLANDS,
    "nederland": TimezoneIANA.NETHERLANDS,
    "hollande": TimezoneIANA.NETHERLANDS,
    
    "autriche": TimezoneIANA.AUSTRIA,
    "austria": TimezoneIANA.AUSTRIA,
    "österreich": TimezoneIANA.AUSTRIA,
    
    # Amérique du Nord
    "états-unis": TimezoneIANA.USA_EASTERN,  # Par défaut Eastern
    "usa": TimezoneIANA.USA_EASTERN,
    "united states": TimezoneIANA.USA_EASTERN,
    
    "canada": TimezoneIANA.CANADA_EASTERN,  # Par défaut Eastern
    
    "mexique": TimezoneIANA.MEXICO,
    "mexico": TimezoneIANA.MEXICO,
    
    # Amérique du Sud
    "brésil": TimezoneIANA.BRAZIL_SAO_PAULO,
    "brazil": TimezoneIANA.BRAZIL_SAO_PAULO,
    "brasil": TimezoneIANA.BRAZIL_SAO_PAULO,
    
    "argentine": TimezoneIANA.ARGENTINA,
    "argentina": TimezoneIANA.ARGENTINA,
    
    "chili": TimezoneIANA.CHILE,
    "chile": TimezoneIANA.CHILE,
    
    # Asie
    "chine": TimezoneIANA.CHINA,
    "china": TimezoneIANA.CHINA,
    
    "japon": TimezoneIANA.JAPAN,
    "japan": TimezoneIANA.JAPAN,
    
    "corée du sud": TimezoneIANA.SOUTH_KOREA,
    "south korea": TimezoneIANA.SOUTH_KOREA,
    
    "inde": TimezoneIANA.INDIA,
    "india": TimezoneIANA.INDIA,
    
    "singapour": TimezoneIANA.SINGAPORE,
    "singapore": TimezoneIANA.SINGAPORE,
    
    "émirats arabes unis": TimezoneIANA.UAE,
    "uae": TimezoneIANA.UAE,
    "dubai": TimezoneIANA.UAE,
    
    # Océanie
    "australie": TimezoneIANA.AUSTRALIA_SYDNEY,  # Par défaut Sydney
    "australia": TimezoneIANA.AUSTRALIA_SYDNEY,
    
    "nouvelle-zélande": TimezoneIANA.NEW_ZEALAND,
    "new zealand": TimezoneIANA.NEW_ZEALAND,
    
    # Afrique
    "afrique du sud": TimezoneIANA.SOUTH_AFRICA,
    "south africa": TimezoneIANA.SOUTH_AFRICA,
    
    "égypte": TimezoneIANA.EGYPT,
    "egypt": TimezoneIANA.EGYPT,
    
    "maroc": TimezoneIANA.MOROCCO,
    "morocco": TimezoneIANA.MOROCCO,
}


def get_timezone_for_country(country: str) -> str:
    """
    Récupère la timezone IANA pour un pays donné.
    
    Args:
        country: Nom du pays (fr, en, de acceptés)
        
    Returns:
        Timezone IANA (ex: "Europe/Zurich") ou None si non trouvé
    """
    country_lower = country.lower().strip()
    timezone = COUNTRY_TO_TIMEZONE.get(country_lower)
    
    if timezone:
        return timezone.value
    
    return None


def get_all_timezones_list() -> List[str]:
    """Retourne la liste de toutes les timezones disponibles."""
    return [tz.value for tz in TimezoneIANA]


def get_timezone_choices_for_tool() -> List[str]:
    """
    Retourne la liste des choix de timezones pour l'outil de l'agent.
    Format adapté pour l'enum dans l'input_schema.
    """
    return get_all_timezones_list()

