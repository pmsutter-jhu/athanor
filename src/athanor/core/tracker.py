"""
ATHANOR Cost & Token Tracker.

This module provides a singleton `TokenTracker` class that monitors LLM usage (tokens) across
the entire execution session. It allows agents to report their usage statistics after each step,
accumulating a total run cost approximation.

Usage:
    from athanor.core.tracker import tracker
    tracker.track(crew_output, stage_name="Drafting")
    tracker.print_summary()
"""
import time
from ..core.config_loader import config

class TokenTracker:
    _instance = None
    
    PRICING = {
        # Gemini 1.5 Series
        "gemini-1.5-flash": (0.075, 0.30),
        "gemini-1.5-pro":   (1.25, 5.00),
        # Gemini 2.x Series 
        "gemini-2.0-flash": (0.10, 0.40), # Approximation
        "gemini-2.5-flash": (0.10, 0.40), # Approximation
        "gemini-2.0-pro":   (1.25, 5.00),
        # OpenAI
        "gpt-4o":           (2.50, 10.00),
        "gpt-4o-mini":      (0.15, 0.60),
        # Anthropic
        "claude-3-5-sonnet":(3.00, 15.00),
        "claude-3-haiku":   (0.25, 1.25)
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenTracker, cls).__new__(cls)
            cls._instance._init_data()
        return cls._instance

    def reset(self):
        """Resets all tracking data for a fresh run."""
        self._init_data()

    def _init_data(self):
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.stages = {}
        self.timers = {}
        self.session_start = time.time()
        
        # Determine pricing model
        self.active_model = config.llm_model.lower()
        self.rates = (0.0, 0.0)
        
        # Fuzzy match model name to pricing
        for key, rates in self.PRICING.items():
            if key in self.active_model:
                self.rates = rates
                break
        
        # Secondary fallback for generic tags
        if self.rates == (0.0, 0.0):
            if "flash" in self.active_model: self.rates = self.PRICING["gemini-1.5-flash"]
            elif "pro" in self.active_model: self.rates = self.PRICING["gemini-1.5-pro"]
            elif "mini" in self.active_model: self.rates = self.PRICING["gpt-4o-mini"]

    def track(self, result_obj, stage_name="General"):
        """Extract token usage from an LLM response, dict, or object with usage attributes."""
        usage = {}
        if hasattr(result_obj, 'token_usage'):
            # Objects exposing token_usage
            usage = result_obj.token_usage
        elif hasattr(result_obj, 'usage_metrics'):
            usage = result_obj.usage_metrics
        elif hasattr(result_obj, 'usage_metadata'):
            # LangChain AIMessage
            usage = result_obj.usage_metadata
        elif isinstance(result_obj, dict):
            if 'token_usage' in result_obj:
                usage = result_obj['token_usage']
            elif 'usage_metadata' in result_obj:
                usage = result_obj['usage_metadata']
            
        if not usage:
            return

        def get_val(obj, key):
            if isinstance(obj, dict):
                return obj.get(key, 0)
            return getattr(obj, key, 0)

        t = get_val(usage, 'total_tokens')
        p = get_val(usage, 'prompt_tokens') or get_val(usage, 'input_tokens')
        c = get_val(usage, 'completion_tokens') or get_val(usage, 'output_tokens')
        
        # params = get_val(usage, 'successful_requests') # sometimes present

        self.total_tokens += t
        self.prompt_tokens += p
        self.completion_tokens += c

        if stage_name not in self.stages:
            self.stages[stage_name] = {'total': 0, 'prompt': 0, 'completion': 0, 'duration': 0.0}
        
        self.stages[stage_name]['total'] += t
        self.stages[stage_name]['prompt'] += p
        self.stages[stage_name]['completion'] += c

    def track_manual_usage(self, stage: str, agent: str, model: str, input_tokens: int, output_tokens: int):
        """Allows manual tracking of tokens without a CrewOutput object."""
        self.total_tokens += (input_tokens + output_tokens)
        self.prompt_tokens += input_tokens
        self.completion_tokens += output_tokens

        if stage not in self.stages:
            self.stages[stage] = {'total': 0, 'prompt': 0, 'completion': 0, 'duration': 0.0}
        
        self.stages[stage]['total'] += (input_tokens + output_tokens)
        self.stages[stage]['prompt'] += input_tokens
        self.stages[stage]['completion'] += output_tokens

    def start_timer(self, stage_name):
        self.timers[stage_name] = time.time()

    def stop_timer(self, stage_name):
        if stage_name in self.timers:
            elapsed = time.time() - self.timers[stage_name]
            if stage_name not in self.stages:
                self.stages[stage_name] = {'total': 0, 'prompt': 0, 'completion': 0, 'duration': 0.0}
            self.stages[stage_name]['duration'] += elapsed
            del self.timers[stage_name]
            return elapsed
        return 0.0

    def calculate_cost(self, prompt_tokens, completion_tokens):
        """Calculates cost in USD."""
        cost_input = (prompt_tokens / 1_000_000) * self.rates[0]
        cost_output = (completion_tokens / 1_000_000) * self.rates[1]
        return cost_input + cost_output

    def get_stage_report(self, stage_name):
        if stage_name in self.stages:
            s = self.stages[stage_name]
            dur = s.get('duration', 0.0)
            cost = self.calculate_cost(s['prompt'], s['completion'])
            return f"   [Perf] {stage_name}: {dur:.2f}s | ~{s['total']} tox (${cost:.4f})"
        return ""
    
    def get_summary_report(self):
        total_time = time.time() - self.session_start
        total_cost = self.calculate_cost(self.prompt_tokens, self.completion_tokens)
        
        report = []
        report.append(f"\n[Performance Metrics]\n       SESSION TOTAL: {total_time:.2f}s | ~{self.total_tokens} tox (P: {self.prompt_tokens}, C: {self.completion_tokens})")
        report.append(f"       ESTIMATED COST: ${total_cost:.4f} (Model: {self.active_model})")
        report.append("       Breakdown:")
        for name, s in self.stages.items():
            dur = s.get('duration', 0.0)
            st_cost = self.calculate_cost(s['prompt'], s['completion'])
            report.append(f"       - {name:<20}: {dur:.2f}s | {s['total']} tox | ${st_cost:.4f}")
        return "\n".join(report)

    def record_to_lifetime_tally(self):
        """Persist this session's totals to the lifetime tally on disk."""
        from .tally import record_session
        try:
            return record_session(
                tokens=self.total_tokens,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                cost=self.calculate_cost(self.prompt_tokens, self.completion_tokens),
                seconds=time.time() - self.session_start,
            )
        except Exception:
            return None

    def get_metrics_data(self):
        """Returns structured data for UI rendering."""
        total_time = time.time() - self.session_start
        total_cost = self.calculate_cost(self.prompt_tokens, self.completion_tokens)

        # Lifetime tally (read-only snapshot)
        try:
            from .tally import load_tally
            lifetime = load_tally()
        except Exception:
            lifetime = {}

        breakdown = []
        for name, s in self.stages.items():
            breakdown.append({
                "stage": name,
                "duration": round(s.get('duration', 0.0), 2),
                "tokens": s['total'],
                "cost": round(self.calculate_cost(s['prompt'], s['completion']), 4)
            })

        return {
            "total_time": round(total_time, 2),
            "total_tokens": self.total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_cost": round(total_cost, 4),
            "model": self.active_model,
            "breakdown": breakdown,
            "lifetime": {
                "tokens": int(lifetime.get("total_tokens", 0)),
                "cost": round(float(lifetime.get("total_cost", 0.0)), 4),
                "runs": int(lifetime.get("total_runs", 0)),
                "seconds": float(lifetime.get("total_seconds", 0.0)),
                "first_run": lifetime.get("first_run"),
            },
        }

tracker = TokenTracker()
