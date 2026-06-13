# Agent-095 — CRAWLER: GitHub (Enhanced with HiViG & WebChallenger)

**Phase:** Cross-phase — Web Crawling  
**Timeline:** Ongoing  
**Committee:** Growth  
**Priority:** HIGH  

---

## Mission

Enhanced GitHub crawling with visual grounding, structured page memory, and intelligent navigation patterns. This agent now has "eyes" and "memory" — it walks across GitHub systematically while understanding page structure and avoiding common web automation pitfalls.

## Enhanced Capabilities

### Visual Grounding for GitHub Navigation
- **Screenshot Validation**: Verify repository cards, buttons, and navigation elements before interaction
- **Coordinate Verification**: Ensure clicks target correct repo links, star buttons, and fork actions
- **Dynamic Content Handling**: Wait for JavaScript-loaded content with visual confirmation

### GitHub-Specific PageMem Structure
```json
{
  "url": "https://github.com/topics/machine-learning",
  "page_type": "topic_page",
  "sections": [
    {
      "level": 1,
      "text": "Machine Learning",
      "summary": "ML topic overview with repository listings",
      "xpath": "//h1",
      "repo_cards": [
        {
          "name": "tensorflow/tensorflow",
          "description": "An Open Source Machine Learning Framework...",
          "stars": 185000,
          "xpath": "//article[@class='border rounded color-shadow-small']//h3/a",
          "star_button_xpath": "//article[contains(.,'tensorflow')]//button[contains(@class,'star')]",
          "fork_button_xpath": "//article[contains(.,'tensorflow')]//button[contains(@class,'fork')]"
        }
      ]
    }
  ],
  "navigation_paths": ["topic_to_repo", "repo_to_maintainer"],
  "pagination": {
    "next_button": "//a[contains(@class,'next_page')]",
    "current_page": 1,
    "total_pages": 50
  }
}
```

### Enhanced Implementation

```python
import requests
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class GitHubRepo:
    repo_url: str
    name: str
    description: str
    stars: int
    forks: int
    license: str
    language: str
    topics: List[str]
    last_commit_date: str
    maintainer_name: str
    maintainer_email: str
    maintainer_twitter: str
    readme_summary: str
    marketplace_category: str
    lead_quality_score: int
    visual_evidence: str  # Base64 screenshot
    page_mem_hash: str    # Hash of PageMem structure

class EnhancedGitHubCrawler(EnhancedWebCrawler):
    def __init__(self, github_token: str):
        super().__init__()
        self.github_token = github_token
        self.api_base = "https://api.github.com"
        self.headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
    def discover_repos_with_visual_grounding(self, topic: str, min_stars: int = 500) -> List[GitHubRepo]:
        """Discover repositories with visual validation"""
        repos = []
        
        # Build PageMem for topic page
        topic_url = f"https://github.com/topics/{topic}"
        page_mem = self.build_page_mem(topic_url)
        
        # Extract repo cards with visual validation
        repo_cards = self.extract_repo_cards_visual(page_mem)
        
        for card in repo_cards:
            if self.visually_validate_repo_card(card):
                repo = self.extract_repo_data_enhanced(card)
                if repo.stars >= min_stars:
                    repos.append(repo)
                    
        return repos
    
    def extract_repo_cards_visual(self, page_mem: Dict) -> List[Dict]:
        """Extract repository cards with visual evidence"""
        repo_cards = []
        
        # Navigate and capture screenshot
        self.driver.get(page_mem["url"])
        time.sleep(2)  # Wait for dynamic content
        screenshot = self.capture_screenshot()
        
        # Extract repo cards using JavaScript
        cards = self.driver.execute_script("""
            const cards = [];
            const articles = document.querySelectorAll('article.border.rounded.color-shadow-small');
            
            articles.forEach(article => {
                const link = article.querySelector('h3 a');
                const description = article.querySelector('p');
                const stars = article.querySelector('a[href*="/stargazers"]');
                const forks = article.querySelector('a[href*="/forks"]');
                const language = article.querySelector('span[itemprop="programmingLanguage"]');
                
                if (link && description) {
                    cards.push({
                        'name': link.textContent.trim(),
                        'url': link.href,
                        'description': description.textContent.trim(),
                        'stars_text': stars ? stars.textContent.trim() : '0',
                        'forks_text': forks ? forks.textContent.trim() : '0',
                        'language': language ? language.textContent.trim() : 'Unknown',
                        'xpath': getXPath(article),
                        'link_xpath': getXPath(link)
                    });
                }
            });
            
            return cards;
            
            function getXPath(element) {
                return document.evaluate(
                    '//' + element.tagName.toLowerCase(), 
                    document, 
                    null, 
                    XPathResult.STRING_TYPE, 
                    null
                ).stringValue;
            }
        """)
        
        for card in cards:
            card["screenshot"] = screenshot
            card["page_mem_hash"] = hash(json.dumps(page_mem, sort_keys=True))
            repo_cards.append(card)
            
        return repo_cards
    
    def visually_validate_repo_card(self, card: Dict) -> bool:
        """Validate repository card before extraction"""
        try:
            # Check if card is visible and contains expected elements
            element = self.driver.find_element("xpath", card["xpath"])
            
            if not element.is_displayed():
                return False
                
            # Verify link is clickable
            link = self.driver.find_element("xpath", card["link_xpath"])
            if not link.is_enabled():
                return False
                
            # Validate star count format
            stars_text = card.get("stars_text", "")
            if not any(stars_text.endswith(unit) for unit in ["k", "M", ""]):
                return False
                
            return True
            
        except Exception as e:
            print(f"Visual validation failed for {card.get('name', 'unknown')}: {e}")
            return False
    
    def extract_repo_data_enhanced(self, card: Dict) -> GitHubRepo:
        """Extract comprehensive repository data with API enrichment"""
        
        # Parse basic info from card
        name = card["name"]
        repo_url = card["url"]
        description = card["description"]
        stars = self.parse_star_count(card["stars_text"])
        forks = self.parse_star_count(card["forks_text"])
        language = card["language"]
        
        # Enrich with API data
        api_data = self.fetch_repo_api_data(name)
        
        # Extract maintainer info
        maintainer_info = self.extract_maintainer_info(api_data)
        
        # Generate lead quality score
        quality_score = self.calculate_lead_quality_score({
            "stars": stars,
            "forks": forks,
            "recent_activity": api_data.get("recent_activity", False),
            "license": api_data.get("license", {}).get("key", ""),
            "has_readme": api_data.get("has_readme", False),
            "topics": api_data.get("topics", [])
        })
        
        return GitHubRepo(
            repo_url=repo_url,
            name=name,
            description=description,
            stars=stars,
            forks=forks,
            license=api_data.get("license", {}).get("name", "None"),
            language=language,
            topics=api_data.get("topics", []),
            last_commit_date=api_data.get("pushed_at", ""),
            maintainer_name=maintainer_info["name"],
            maintainer_email=maintainer_info["email"],
            maintainer_twitter=maintainer_info["twitter"],
            readme_summary=api_data.get("readme_summary", ""),
            marketplace_category=self.categorize_repo(api_data.get("topics", []), language),
            lead_quality_score=quality_score,
            visual_evidence=card["screenshot"],
            page_mem_hash=card["page_mem_hash"]
        )
    
    def fetch_repo_api_data(self, repo_name: str) -> Dict:
        """Fetch additional data via GitHub API"""
        try:
            owner, repo = repo_name.split("/")
            
            # Basic repo info
            repo_response = requests.get(
                f"{self.api_base}/repos/{owner}/{repo}",
                headers=self.headers
            )
            repo_data = repo_response.json()
            
            # README content
            readme_response = requests.get(
                f"{self.api_base}/repos/{owner}/{repo}/readme",
                headers=self.headers
            )
            readme_summary = ""
            if readme_response.status_code == 200:
                readme_content = requests.get(readme_data["download_url"]).text
                readme_summary = readme_content[:500] + "..." if len(readme_content) > 500 else readme_content
            
            # Recent activity (last 30 days)
            commits_response = requests.get(
                f"{self.api_base}/repos/{owner}/{repo}/commits",
                headers=self.headers,
                params={"since": (datetime.now() - timedelta(days=30)).isoformat()}
            )
            recent_activity = len(commits_response.json()) > 0
            
            return {
                **repo_data,
                "readme_summary": readme_summary,
                "recent_activity": recent_activity,
                "has_readme": readme_response.status_code == 200
            }
            
        except Exception as e:
            print(f"API fetch failed for {repo_name}: {e}")
            return {}
    
    def extract_maintainer_info(self, api_data: Dict) -> Dict:
        """Extract maintainer contact information"""
        owner = api_data.get("owner", {})
        
        # Get owner details
        owner_response = requests.get(
            owner["url"],
            headers=self.headers
        )
        owner_data = owner_response.json()
        
        # Extract email from commits if not public
        email = owner_data.get("email", "")
        if not email:
            commits_response = requests.get(
                f"{self.api_base}/repos/{api_data['full_name']}/commits",
                headers=self.headers,
                params={"per_page": 1}
            )
            if commits_response.status_code == 200:
                email = commits_response.json()[0]["commit"]["author"]["email"]
        
        return {
            "name": owner_data.get("name", owner.get("login", "")),
            "email": email,
            "twitter": owner_data.get("twitter_username", "")
        }
    
    def parse_star_count(self, stars_text: str) -> int:
        """Parse star count from text (e.g., '1.5k' -> 1500)"""
        if not stars_text:
            return 0
            
        stars_text = stars_text.lower().replace(",", "")
        
        if stars_text.endswith("k"):
            return int(float(stars_text[:-1]) * 1000)
        elif stars_text.endswith("m"):
            return int(float(stars_text[:-1]) * 1000000)
        else:
            return int(stars_text)
    
    def calculate_lead_quality_score(self, repo_data: Dict) -> int:
        """Calculate lead quality score (0-100)"""
        score = 0
        
        # Base score for stars
        stars = repo_data.get("stars", 0)
        if stars >= 10000:
            score += 30
        elif stars >= 5000:
            score += 25
        elif stars >= 1000:
            score += 20
        elif stars >= 500:
            score += 15
        
        # Recent activity bonus
        if repo_data.get("recent_activity", False):
            score += 20
        
        # License compatibility
        license_key = repo_data.get("license", "")
        if license_key in ["mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause"]:
            score += 15
        elif license_key:
            score += 10
        
        # README presence
        if repo_data.get("has_readme", False):
            score += 10
        
        # Topic relevance
        topics = repo_data.get("topics", [])
        ml_topics = ["machine-learning", "deep-learning", "neural-network", "ai", "llm", "nlp"]
        if any(topic in topics for topic in ml_topics):
            score += 15
        
        # Fork activity
        forks = repo_data.get("forks", 0)
        if forks >= stars * 0.3:  # Good fork-to-star ratio
            score += 10
        
        return min(score, 100)
    
    def categorize_repo(self, topics: List[str], language: str) -> str:
        """Categorize repository for marketplace"""
        topic_lower = [t.lower() for t in topics]
        language_lower = language.lower()
        
        if any(topic in topic_lower for topic in ["nlp", "text", "transformer", "bert", "gpt"]):
            return "NLP"
        elif any(topic in topic_lower for topic in ["computer-vision", "image", "cnn", "detection"]):
            return "Computer Vision"
        elif any(topic in topic_lower for topic in ["reinforcement-learning", "rl", "gym", "agent"]):
            return "Reinforcement Learning"
        elif any(topic in topic_lower for topic in ["mlops", "deployment", "serving"]):
            return "MLOps"
        elif language_lower in ["python", "javascript", "typescript"]:
            return "General AI/ML"
        else:
            return "Other"

## Enhanced Search Queries with Visual Validation

```python
enhanced_search_queries = [
    {
        "query": "topic:machine-learning stars:>500 pushed:>2026-01-01",
        "validation_criteria": ["repo_card", "star_count", "recent_activity"],
        "compound_workflow": "ml_topic_discovery"
    },
    {
        "query": "topic:llm stars:>200 pushed:>2026-01-01",
        "validation_criteria": ["repo_card", "model_files", "documentation"],
        "compound_workflow": "llm_model_discovery"
    },
    {
        "query": "topic:ai-tools stars:>100 pushed:>2026-03-01",
        "validation_criteria": ["tool_interface", "cli_presence", "examples"],
        "compound_workflow": "ai_tools_discovery"
    }
]
```

## Enhanced Success Metrics

| Metric | Target | Research-Based Improvement |
|---|---|---|
| Visual validation accuracy | > 98% | +5.8% from HiViG |
| Error reduction | < 1% | -9.0% execution errors |
| Data completeness | > 95% | API + visual enrichment |
| Lead quality accuracy | > 85% | Enhanced scoring algorithm |
| Cross-platform success | > 97% | Consistent across browsers |

## Enhanced Tasks

1. **Visual Discovery**: Implement screenshot-based repo card validation
2. **PageMem Construction**: Build structured GitHub page representations
3. **API-Visual Fusion**: Combine API data with visual evidence
4. **Compound Workflows**: Create reusable discovery patterns
5. **Quality Scoring**: Enhanced lead quality assessment
6. **Evidence Generation**: Hash-chained visual evidence for all discoveries

## Dependencies

- Agent-094 (enhanced crawler lead)
- Agent-010 (GitHub vendor hunter)
- Agent-030 (outreach lead)
- Agent-072 (evidence scientist)

---

## Research References

1. **HiViG**: History-Aware Visually Grounded Critic for Computer Use Agents
2. **WebChallenger**: Reliable and Efficient Generalist Web Agent  
3. **MemVenom**: Memory Poisoning Defenses for Evidence Validation
