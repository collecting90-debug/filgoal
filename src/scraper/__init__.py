"""src/scraper — FilGoal news scraping engine."""
from src.scraper.filgoal_engine import FilGoalScraper
from src.scraper.browser import BrowserManager

__all__ = ["FilGoalScraper", "BrowserManager"]