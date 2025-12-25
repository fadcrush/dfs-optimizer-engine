"""
NBA DFS Models - Complete Analytics Suite
Tracks slates, contests, lineups, player performance, and ownership models
"""

from django.db import models
from django.db.models import Avg, Sum, Count, Q, F
from django.core.validators import MinValueValidator, MaxValueValidator
from apps.common.models import TimeStampedModel
from decimal import Decimal
import json


class NbaSlate(TimeStampedModel):
    """
    DFS Slate (contest date/time/type)
    Represents a group of games at a specific time
    """
    date = models.DateField(db_index=True)
    site = models.CharField(
        max_length=20,
        choices=[('FD', 'FanDuel'), ('DK', 'DraftKings')],
        db_index=True
    )
    name = models.CharField(max_length=100, blank=True, help_text="e.g., 'Main', 'Early', 'Late'")
    game_count = models.IntegerField(default=0, help_text="Number of games in slate")
    
    # Aggregate statistics
    total_contests = models.IntegerField(default=0)
    total_entries = models.IntegerField(default=0)
    total_invested = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_winnings = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Metadata
    is_complete = models.BooleanField(default=False, help_text="All contests graded")
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-date', 'site']
        unique_together = ['date', 'site', 'name']
        indexes = [
            models.Index(fields=['-date', 'site']),
            models.Index(fields=['is_complete']),
        ]
    
    def __str__(self):
        return f"{self.site} {self.date} - {self.name or 'Main'}"
    
    @property
    def roi_percentage(self):
        """Calculate ROI for this slate"""
        if self.total_invested > 0:
            return ((self.total_winnings - self.total_invested) / self.total_invested) * 100
        return 0
    
    @property
    def profit(self):
        """Net profit/loss for this slate"""
        return self.total_winnings - self.total_invested
    
    @property
    def avg_entry_size(self):
        """Average entry fee"""
        if self.total_entries > 0:
            return self.total_invested / self.total_entries
        return 0
    
    def update_totals(self):
        """Recalculate aggregate statistics from contests"""
        contests = self.contests.all()
        self.total_contests = contests.count()
        self.total_entries = sum(c.my_entries_count for c in contests)
        self.total_invested = sum(c.total_invested for c in contests)
        self.total_winnings = sum(c.total_won for c in contests)
        self.save()


class NbaContest(TimeStampedModel):
    """
    Individual DFS Contest
    Represents a specific tournament or cash game
    """
    slate = models.ForeignKey(NbaSlate, on_delete=models.CASCADE, related_name='contests')
    
    # Contest identification
    contest_name = models.CharField(max_length=200, db_index=True)
    contest_id = models.CharField(max_length=100, blank=True, unique=True, null=True)
    
    # Contest details
    entry_fee = models.DecimalField(max_digits=10, decimal_places=2)
    total_entries = models.IntegerField(default=0, help_text="Total entries in contest")
    prize_pool = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    
    # Contest type
    CONTEST_TYPES = [
        ('GPP', 'GPP - Tournament'),
        ('CASH', 'Cash Game'),
        ('50/50', '50/50'),
        ('DOUBLE_UP', 'Double Up'),
        ('H2H', 'Head to Head'),
        ('MULTIPLIER', 'Multiplier'),
        ('SATELLITE', 'Satellite'),
    ]
    contest_type = models.CharField(max_length=20, choices=CONTEST_TYPES, default='GPP', db_index=True)
    
    # Payout structure
    payout_places = models.IntegerField(null=True, blank=True, help_text="Number of places paid")
    min_cash_line = models.IntegerField(null=True, blank=True, help_text="Rank needed to cash")
    
    # Status
    is_complete = models.BooleanField(default=False, db_index=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slate', 'contest_type']),
            models.Index(fields=['is_complete']),
        ]
    
    def __str__(self):
        return f"{self.contest_name} - ${self.entry_fee}"
    
    @property
    def my_entries_count(self):
        """Count of my entries in this contest"""
        return self.entries.count()
    
    @property
    def total_invested(self):
        """Total amount invested"""
        return self.entry_fee * self.my_entries_count
    
    @property
    def total_won(self):
        """Total winnings"""
        return self.entries.aggregate(Sum('payout'))['payout__sum'] or Decimal('0')
    
    @property
    def profit(self):
        """Net profit/loss"""
        return self.total_won - self.total_invested
    
    @property
    def roi(self):
        """ROI percentage"""
        if self.total_invested > 0:
            return ((self.total_won - self.total_invested) / self.total_invested) * 100
        return 0
    
    @property
    def cash_rate(self):
        """Percentage of entries that cashed"""
        total = self.my_entries_count
        if total > 0:
            cashed = self.entries.filter(cashed=True).count()
            return (cashed / total) * 100
        return 0
    
    @property
    def top_10_rate(self):
        """Percentage of entries in top 10%"""
        total = self.my_entries_count
        if total > 0:
            top_10 = self.entries.filter(top_10_percent=True).count()
            return (top_10 / total) * 100
        return 0


class NbaLineupEntry(TimeStampedModel):
    """
    Individual Lineup Entry in a Contest
    Represents one lineup submission
    """
    contest = models.ForeignKey(NbaContest, on_delete=models.CASCADE, related_name='entries')
    entry_id = models.CharField(max_length=100, blank=True, db_index=True)
    
    # Lineup composition (stored as JSON for flexibility)
    lineup_data = models.JSONField(
        default=dict,
        help_text="Full lineup with player IDs, names, positions, salaries"
    )
    
    # Projections
    projected_points = models.FloatField(null=True, blank=True)
    projected_ownership = models.FloatField(null=True, blank=True, help_text="Avg ownership %")
    
    # Actual Performance
    actual_points = models.FloatField(null=True, blank=True, db_index=True)
    
    # Ownership metrics
    avg_ownership = models.FloatField(null=True, blank=True, help_text="Actual avg ownership")
    unique_percentage = models.FloatField(
        null=True, 
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="How unique was this lineup (0-100%)"
    )
    
    # Contest results
    final_rank = models.IntegerField(null=True, blank=True, db_index=True)
    percentile = models.FloatField(
        null=True, 
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percentile finish (0-100, lower is better)"
    )
    payout = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Performance flags
    cashed = models.BooleanField(default=False, db_index=True)
    top_1_percent = models.BooleanField(default=False)
    top_5_percent = models.BooleanField(default=False)
    top_10_percent = models.BooleanField(default=False, db_index=True)
    
    # Strategy metadata
    construction_strategy = models.CharField(
        max_length=50,
        blank=True,
        help_text="e.g., 'Balanced', 'Stars and Scrubs', 'Contrarian'"
    )
    stack_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="e.g., 'None', '2-man stack', 'Game stack'"
    )
    
    class Meta:
        ordering = ['final_rank']
        indexes = [
            models.Index(fields=['contest', 'final_rank']),
            models.Index(fields=['cashed', 'top_10_percent']),
            models.Index(fields=['-actual_points']),
        ]
    
    def __str__(self):
        rank_str = f"Rank {self.final_rank}" if self.final_rank else "Pending"
        return f"Entry #{self.entry_id or self.id} - {rank_str}"
    
    @property
    def profit(self):
        """Profit for this entry"""
        return self.payout - self.contest.entry_fee
    
    @property
    def projection_accuracy(self):
        """How accurate was the projection? (percentage)"""
        if self.projected_points and self.actual_points:
            diff = abs(self.projected_points - self.actual_points)
            accuracy = 100 - min((diff / self.projected_points * 100), 100)
            return max(accuracy, 0)  # Don't go negative
        return None
    
    @property
    def ownership_edge(self):
        """Difference between projected and actual ownership"""
        if self.projected_ownership and self.avg_ownership:
            return self.projected_ownership - self.avg_ownership
        return None
    
    def save(self, *args, **kwargs):
        """Auto-calculate performance flags"""
        if self.percentile is not None:
            self.top_1_percent = self.percentile <= 1.0
            self.top_5_percent = self.percentile <= 5.0
            self.top_10_percent = self.percentile <= 10.0
        
        if self.payout and self.contest:
            self.cashed = self.payout > self.contest.entry_fee
        
        super().save(*args, **kwargs)


class NbaPlayerPerformance(TimeStampedModel):
    """
    Individual Player Performance in a Slate
    Tracks actual vs projected for each player
    """
    slate = models.ForeignKey(NbaSlate, on_delete=models.CASCADE, related_name='player_performances')
    
    # Player identification
    dfs_id = models.CharField(max_length=50, db_index=True)
    name = models.CharField(max_length=100, db_index=True)
    position = models.CharField(max_length=20)
    team = models.CharField(max_length=10, db_index=True)
    opponent = models.CharField(max_length=10, blank=True)
    
    # Game context
    game_location = models.CharField(
        max_length=10,
        choices=[('HOME', 'Home'), ('AWAY', 'Away')],
        blank=True
    )
    minutes_played = models.FloatField(null=True, blank=True)
    
    # DFS details
    salary = models.IntegerField()
    projected_ownership = models.FloatField(null=True, blank=True)
    actual_ownership = models.FloatField(null=True, blank=True)
    
    # Performance
    projected_points = models.FloatField(db_index=True)
    actual_points = models.FloatField(null=True, blank=True, db_index=True)
    
    # Value metrics
    value_projected = models.FloatField(
        null=True,
        blank=True,
        help_text="Projected points per $1000"
    )
    value_actual = models.FloatField(
        null=True,
        blank=True,
        help_text="Actual points per $1000"
    )
    
    # Game stats (optional - for deeper analysis)
    game_stats = models.JSONField(
        default=dict,
        blank=True,
        help_text="Points, rebounds, assists, etc."
    )
    
    class Meta:
        ordering = ['-actual_points']
        unique_together = ['slate', 'dfs_id']
        indexes = [
            models.Index(fields=['slate', 'team']),
            models.Index(fields=['-actual_points']),
            models.Index(fields=['salary']),
        ]
    
    def __str__(self):
        return f"{self.name} - {self.slate.date} ({self.actual_points or 'TBD'} pts)"
    
    def save(self, *args, **kwargs):
        """Auto-calculate value metrics"""
        if self.salary > 0:
            self.value_projected = (self.projected_points / self.salary) * 1000
            if self.actual_points is not None:
                self.value_actual = (self.actual_points / self.salary) * 1000
        super().save(*args, **kwargs)
    
    @property
    def projection_diff(self):
        """Difference between actual and projected"""
        if self.actual_points is not None:
            return self.actual_points - self.projected_points
        return None
    
    @property
    def outperformed(self):
        """Did player beat projection?"""
        diff = self.projection_diff
        return diff > 0 if diff is not None else None


class OwnershipModel(TimeStampedModel):
    """
    Machine Learning Model for Ownership Prediction
    Stores model parameters and performance metrics
    """
    slate = models.ForeignKey(NbaSlate, on_delete=models.CASCADE, related_name='ownership_models')
    model_version = models.CharField(max_length=50, default='v1.0')
    
    # Model configuration
    features_used = models.JSONField(
        default=list,
        help_text="List of features: ['salary', 'proj_points', 'value', 'team', 'position']"
    )
    weights = models.JSONField(
        default=dict,
        help_text="Feature weights: {'salary': 0.3, 'value': 0.5, ...}"
    )
    
    # Performance metrics
    mean_absolute_error = models.FloatField(null=True, blank=True)
    root_mean_squared_error = models.FloatField(null=True, blank=True)
    r_squared = models.FloatField(null=True, blank=True)
    
    # Training info
    training_samples = models.IntegerField(default=0)
    trained_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slate', 'model_version']),
        ]
    
    def __str__(self):
        return f"Ownership Model {self.model_version} - {self.slate.date}"


class PlayerCorrelation(TimeStampedModel):
    """
    Player-to-Player Correlation Data
    Tracks how players perform together
    """
    # Player pair
    player1_id = models.CharField(max_length=50, db_index=True)
    player1_name = models.CharField(max_length=100)
    player2_id = models.CharField(max_length=50, db_index=True)
    player2_name = models.CharField(max_length=100)
    
    # Correlation metrics
    correlation_coefficient = models.FloatField(
        validators=[MinValueValidator(-1.0), MaxValueValidator(1.0)],
        help_text="Pearson correlation (-1 to 1)"
    )
    games_together = models.IntegerField(default=0)
    
    # Context
    same_team = models.BooleanField(default=False)
    position_group = models.CharField(
        max_length=50,
        blank=True,
        help_text="e.g., 'Guards', 'Frontcourt', 'All'"
    )
    
    # Statistics
    avg_combined_points = models.FloatField(null=True, blank=True)
    
    class Meta:
        ordering = ['-correlation_coefficient']
        unique_together = ['player1_id', 'player2_id']
        indexes = [
            models.Index(fields=['-correlation_coefficient']),
            models.Index(fields=['same_team']),
        ]
    
    def __str__(self):
        return f"{self.player1_name} + {self.player2_name} ({self.correlation_coefficient:.2f})"


class StrategyPerformance(TimeStampedModel):
    """
    Performance tracking for different lineup construction strategies
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    
    # Performance metrics
    total_lineups = models.IntegerField(default=0)
    total_invested = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_won = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Win rates
    cash_rate = models.FloatField(default=0, help_text="% of lineups that cashed")
    top_10_rate = models.FloatField(default=0, help_text="% in top 10%")
    top_1_rate = models.FloatField(default=0, help_text="% in top 1%")
    
    # Avg metrics
    avg_rank = models.FloatField(null=True, blank=True)
    avg_percentile = models.FloatField(null=True, blank=True)
    
    class Meta:
        ordering = ['-total_won']
        verbose_name_plural = "Strategy performances"
    
    def __str__(self):
        return f"{self.name} - {self.roi:.1f}% ROI"
    
    @property
    def roi(self):
        """ROI percentage"""
        if self.total_invested > 0:
            return ((self.total_won - self.total_invested) / self.total_invested) * 100
        return 0
    
    def update_from_lineups(self, lineup_queryset):
        """Recalculate metrics from lineup entries"""
        lineups = lineup_queryset.filter(construction_strategy=self.name)
        
        self.total_lineups = lineups.count()
        if self.total_lineups == 0:
            return
        
        # Sum investments and winnings
        total_fee = sum(l.contest.entry_fee for l in lineups)
        total_payout = lineups.aggregate(Sum('payout'))['payout__sum'] or 0
        
        self.total_invested = total_fee
        self.total_won = total_payout
        
        # Win rates
        self.cash_rate = (lineups.filter(cashed=True).count() / self.total_lineups) * 100
        self.top_10_rate = (lineups.filter(top_10_percent=True).count() / self.total_lineups) * 100
        self.top_1_rate = (lineups.filter(top_1_percent=True).count() / self.total_lineups) * 100
        
        # Averages
        self.avg_rank = lineups.aggregate(Avg('final_rank'))['final_rank__avg']
        self.avg_percentile = lineups.aggregate(Avg('percentile'))['percentile__avg']
        
        self.save()