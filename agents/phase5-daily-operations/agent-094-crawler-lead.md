# Agent-094 — CRAWLER LEAD (Enhanced with HiViG & WebChallenger)

**Phase:** Cross-phase — Web Crawling & Navigation  
**Timeline:** Ongoing  
**Committee:** Growth  
**Priority:** HIGH  

---

## Mission

Lead the crawler agent squad with enhanced web navigation capabilities based on latest research. These agents now have "eyes" and "memory" — they traverse the web with visual grounding, historical awareness, and structured page representations.

## Enhanced Capabilities (Based on Latest Research)

### HiViG Integration (History-aware Visually Grounded)
- **Visual Grounding**: Screenshot analysis before action execution to verify UI elements
- **Macro-action History**: Compact records of completed achievements and navigation patterns
- **Cross-platform Generalization**: Consistent performance across web, mobile, and desktop interfaces
- **Error Interception**: Pre-execution validation to prevent clicking wrong elements

### WebChallenger PageMem Framework
- **Structured Page Representation**: DOM-based hierarchical semantic sections with summaries
- **Selective Attention**: Skim section summaries, extract details only from task-relevant regions
- **Persistent Memory**: Reusable maps of pages and element behaviors
- **Compound Action Workflows**: Multi-step interactions collapsed into single agent actions

### Enhanced Implementation

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import base64
from PIL import Image
import io
from typing import Dict, List, Tuple
import json

class EnhancedWebCrawler:
    def __init__(self):
        self.options = Options()
        self.options.add_argument('--headless')
        self.options.add_argument('--no-sandbox')
        self.options.add_argument('--disable-dev-shm-usage')
        
        # Enhanced visual capabilities
        self.options.add_argument('--window-size=1920,1080')
        self.options.add_argument('--force-device-scale-factor=1')
        
        self.driver = webdriver.Chrome(options=self.options)
        self.macro_action_history = []
        self.page_memory = {}  # PageMem storage
        
    def capture_screenshot(self) -> str:
        """Capture screenshot for visual grounding"""
        screenshot = self.driver.get_screenshot_as_png()
        return base64.b64encode(screenshot).decode('utf-8')
    
    def build_page_mem(self, url: str) -> Dict:
        """Build structured PageMem representation"""
        self.driver.get(url)
        
        # Extract DOM structure
        page_structure = {
            "url": url,
            "title": self.driver.title,
            "sections": [],
            "interactive_elements": [],
            "navigation_paths": []
        }
        
        # Extract semantic sections
        sections = self.driver.execute_script("""
            const sections = [];
            const headers = document.querySelectorAll('h1, h2, h3, h4, h5, h6');
            headers.forEach(header => {
                const section = {
                    'level': parseInt(header.tagName.charAt(1)),
                    'text': header.textContent.trim(),
                    'summary': header.textContent.trim().substring(0, 100),
                    'xpath': getXPath(header),
                    'elements': []
                };
                
                // Find elements within this section
                let next = header.nextElementSibling;
                while (next && !next.matches('h1, h2, h3, h4, h5, h6')) {
                    if (next.matches('a, button, input, select')) {
                        section.elements.push({
                            'tag': next.tagName,
                            'text': next.textContent.trim(),
                            'xpath': getXPath(next),
                            'action_type': getActionType(next)
                        });
                    }
                    next = next.nextElementSibling;
                }
                
                sections.push(section);
            });
            return sections;
            
            function getXPath(element) {
                return document.evaluate(
                    '//' + element.tagName.toLowerCase(), 
                    document, 
                    null, 
                    XPathResult.STRING_TYPE, 
                    null
                ).stringValue;
            }
            
            function getActionType(element) {
                if (element.matches('a')) return 'navigation';
                if (element.matches('button')) return 'action';
                if (element.matches('input')) return element.type;
                if (element.matches('select')) return 'selection';
                return 'unknown';
            }
        """)
        
        page_structure["sections"] = sections
        
        # Store in PageMem
        self.page_memory[url] = page_structure
        return page_structure
    
    def visually_validate_action(self, element_xpath: str, action_type: str) -> bool:
        """Validate action before execution using visual grounding"""
        screenshot = self.capture_screenshot()
        
        # Visual validation logic (simplified)
        try:
            element = self.driver.find_element("xpath", element_xpath)
            
            # Check if element is visible and clickable
            if not element.is_displayed():
                return False
                
            if action_type in ["click", "navigation"] and not element.is_enabled():
                return False
                
            return True
            
        except Exception:
            return False
    
    def execute_compound_action(self, workflow: Dict) -> bool:
        """Execute compound action workflow"""
        for step in workflow["steps"]:
            if not self.visually_validate_action(step["xpath"], step["action"]):
                print(f"Visual validation failed for step: {step}")
                return False
                
            if step["action"] == "click":
                self.driver.find_element("xpath", step["xpath"]).click()
            elif step["action"] == "type":
                element = self.driver.find_element("xpath", step["xpath"])
                element.clear()
                element.send_keys(step["text"])
            elif step["action"] == "scroll":
                self.driver.execute_script("window.scrollTo(0, arguments[0]);", step["position"])
                
        return True
    
    def add_to_macro_history(self, action: Dict):
        """Add completed action to macro-action history"""
        self.macro_action_history.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "achievement": action.get("achievement", ""),
            "page_context": action.get("page_context", "")
        })
        
        # Keep only recent history (last 50 actions)
        if len(self.macro_action_history) > 50:
            self.macro_action_history = self.macro_action_history[-50:]
```

## Research-Backed Enhancements

### 1. Visual Grounding System
- **Pre-execution validation**: Screenshot analysis before each action
- **Coordinate verification**: Ensure clicks target correct UI elements
- **Error reduction**: 5.8% improvement for Qwen3-VL-32B, 9.0% for Gemini-3-Flash

### 2. PageMem Structure
```json
{
  "url": "https://github.com/topics/machine-learning",
  "title": "Machine Learning Topics",
  "sections": [
    {
      "level": 1,
      "text": "Machine Learning",
      "summary": "Machine learning topic overview",
      "xpath": "//h1",
      "elements": [
        {
          "tag": "a",
          "text": "Explore topics",
          "xpath": "//a[contains(text(),'Explore')]",
          "action_type": "navigation"
        }
      ]
    }
  ],
  "navigation_paths": ["topic_navigation", "repo_discovery"],
  "last_visited": "2026-06-10T00:30:00Z"
}
```

### 3. Compound Action Workflows
```python
# Example: GitHub repository discovery workflow
github_discovery_workflow = {
    "name": "discover_ml_repos",
    "steps": [
        {
            "action": "navigate",
            "url": "https://github.com/topics/machine-learning",
            "xpath": "//body"
        },
        {
            "action": "scroll",
            "position": 500,
            "xpath": "//body"
        },
        {
            "action": "click",
            "xpath": "//a[contains(@href,'/topics/machine-learning')]",
            "achievement": "Navigate to ML topic page"
        },
        {
            "action": "extract_repos",
            "xpath": "//article[@class='border rounded color-shadow-small']",
            "achievement": "Extract repository information"
        }
    ]
}
```

## Enhanced Success Metrics

| Metric | Target | Research-Based Improvement |
|---|---|---|
| Navigation accuracy | > 98% | +5.8% (HiViG visual grounding) |
| Error reduction | < 2% | -9.0% execution errors |
| Cross-platform success | > 95% | Consistent across web/mobile/desktop |
| Memory efficiency | 40% reduction | PageMem selective attention |
| Compound workflow success | > 90% | Multi-step action automation |

## Managed Agents (Enhanced)

| Agent | Specialization | Enhancement |
|---|---|---|
| Agent-095 | GitHub Crawler | Visual repo discovery, structured page memory |
| Agent-096 | HuggingFace Crawler | Model card extraction, multimodal validation |
| Agent-097 | Market Intelligence Crawler | Competitive analysis, cross-site correlation |

## Enhanced Tasks

1. **Visual Navigation**: Implement screenshot-based action validation
2. **PageMem Construction**: Build structured representations for each platform
3. **Macro-action History**: Track achievements and navigation patterns
4. **Compound Workflows**: Create reusable multi-step interaction patterns
5. **Cross-platform Learning**: Share navigation patterns between agents
6. **Evidence Generation**: Create hash-chained evidence bundles for all actions

## Security & Memory Safety

### MemVenom-Style Defenses
- **Memory Validation**: Verify integrity of stored page representations
- **Trigger Detection**: Identify potential malicious content injection
- **Cross-tenant Isolation**: Ensure no memory leakage between different crawling tasks
- **Audit Trails**: Hash-chain verification for all stored memories

## Dependencies

- Agent-095-097 (enhanced crawler squad)
- Agent-010-029 (vendor hunters consume enhanced crawler data)
- Agent-030 (outreach lead — receives qualified leads with visual evidence)
- Agent-065 (memory scientist — provides memory safety validation)
- Agent-072 (evidence scientist — validates evidence bundles)

---

## Research References

1. **HiViG**: History-Aware Visually Grounded Critic for Computer Use Agents (arXiv:2606.11078)
2. **WebChallenger**: Reliable and Efficient Generalist Web Agent (arXiv:2606.10423)
3. **MemVenom**: Triggered Poisoning of Multimodal Memories in Web Agents (arXiv:2606.10742)
