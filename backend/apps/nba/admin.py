"""
NBA Admin Interface
Enhanced admin for managing DFS data
"""

from django.contrib import admin
from django.db.models import Sum, Avg, Count
from django.utils.html import format_html
from .models import (
    NbaSlate,
    NbaContest,
    NbaLineupEntry,
    NbaPlayerPerformance,
    OwnershipModel,
    PlayerCorrelation,
    StrategyPerformance
)


@admin.register(NbaSlate)
class NbaSlateAdmin(admin.ModelAdmin):
    list_display = (
        'date', 'site', 'name', 'game_count',
        'total_contests', 'total_entries',
        'invested_display', 'won_display', 'roi_display',
        'is_complete'
    )
    list_filter = ('site', 'is_complete', 'date')
    search_fields = ('name', 'date')
    date_hierarchy = 'date'
    ordering = ('-date', 'site')
    
    readonly_fields = ('created_at', 'updated_at', 'roi_percentage', 'profit')
    
    fieldsets = (
        ('Slate Information', {
            'fields': ('date', 'site', 'name', 'game_count', 'is_complete')
        }),
        ('Performance Summary', {
            'fields': (
                'total_contests', 'total_entries',
                'total_invested', 'total_winnings',
                'roi_percentage', 'profit'
            )
        }),
        ('Notes', {
            'fields': ('notes',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def invested_display(self, obj):
        return f"${obj.total_invested:,.2f}"
    invested_display.short_description = 'Invested'
    
    def won_display(self, obj):
        return f"${obj.total_winnings:,.2f}"
    won_display.short_description = 'Won'
    
    def roi_display(self, obj):
        roi = obj.roi_percentage
        color = 'green' if roi > 0 else 'red' if roi < 0 else 'gray'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.1f}%</span>',
            color, roi
        )
    roi_display.short_description = 'ROI'
    
    actions = ['update_slate_totals']
    
    def update_slate_totals(self, request, queryset):
        for slate in queryset:
            slate.update_totals()
        self.message_user(request, f"Updated totals for {queryset.count()} slates")
    update_slate_totals.short_description = "Update slate totals"


@admin.register(NbaContest)
class NbaContestAdmin(admin.ModelAdmin):
    list_display = (
        'contest_name', 'slate', 'contest_type',
        'entry_fee', 'my_entries', 'invested_display',
        'won_display', 'roi_display', 'is_complete'
    )
    list_filter = ('contest_type', 'is_complete', 'slate__site', 'slate__date')
    search_fields = ('contest_name', 'contest_id')
    date_hierarchy = 'created_at'
    
    readonly_fields = ('my_entries_count', 'total_invested', 'total_won', 'roi', 'cash_rate')
    
    fieldsets = (
        ('Contest Details', {
            'fields': (
                'slate', 'contest_name', 'contest_id',
                'contest_type', 'entry_fee'
            )
        }),
        ('Contest Info', {
            'fields': (
                'total_entries', 'prize_pool',
                'payout_places', 'min_cash_line'
            )
        }),
        ('My Performance', {
            'fields': (
                'my_entries_count', 'total_invested',
                'total_won', 'roi', 'cash_rate'
            )
        }),
        ('Status', {
            'fields': ('is_complete',)
        }),
    )
    
    def my_entries(self, obj):
        return obj.my_entries_count
    my_entries.short_description = 'Entries'
    
    def invested_display(self, obj):
        return f"${obj.total_invested:,.2f}"
    invested_display.short_description = 'Invested'
    
    def won_display(self, obj):
        return f"${obj.total_won:,.2f}"
    won_display.short_description = 'Won'
    
    def roi_display(self, obj):
        roi = obj.roi
        color = 'green' if roi > 0 else 'red' if roi < 0 else 'gray'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.1f}%</span>',
            color, roi
        )
    roi_display.short_description = 'ROI'


@admin.register(NbaLineupEntry)
class NbaLineupEntryAdmin(admin.ModelAdmin):
    list_display = (
        'entry_id', 'contest_short', 'projected_points',
        'actual_points', 'rank_display', 'payout_display',
        'cashed_display', 'top_10_display'
    )
    list_filter = (
        'cashed', 'top_10_percent', 'top_1_percent',
        'contest__contest_type', 'contest__slate__site'
    )
    search_fields = ('entry_id', 'contest__contest_name')
    date_hierarchy = 'created_at'
    
    readonly_fields = (
        'profit', 'projection_accuracy',
        'ownership_edge', 'percentile'
    )
    
    fieldsets = (
        ('Entry Info', {
            'fields': ('contest', 'entry_id', 'lineup_data')
        }),
        ('Projections', {
            'fields': (
                'projected_points', 'projected_ownership',
                'construction_strategy', 'stack_type'
            )
        }),
        ('Actual Performance', {
            'fields': (
                'actual_points', 'avg_ownership',
                'unique_percentage'
            )
        }),
        ('Results', {
            'fields': (
                'final_rank', 'percentile', 'payout',
                'profit', 'projection_accuracy', 'ownership_edge'
            )
        }),
        ('Flags', {
            'fields': (
                'cashed', 'top_1_percent',
                'top_5_percent', 'top_10_percent'
            )
        }),
    )
    
    def contest_short(self, obj):
        return f"{obj.contest.contest_name[:30]}..."
    contest_short.short_description = 'Contest'
    
    def rank_display(self, obj):
        if obj.final_rank:
            if obj.top_1_percent:
                color = 'gold'
            elif obj.top_10_percent:
                color = 'green'
            elif obj.cashed:
                color = 'blue'
            else:
                color = 'red'
            return format_html(
                '<span style="color: {}; font-weight: bold;">#{}</span>',
                color, obj.final_rank
            )
        return '-'
    rank_display.short_description = 'Rank'
    
    def payout_display(self, obj):
        color = 'green' if obj.payout > obj.contest.entry_fee else 'red'
        return format_html(
            '<span style="color: {};">${:.2f}</span>',
            color, obj.payout
        )
    payout_display.short_description = 'Payout'
    
    def cashed_display(self, obj):
        return '✓' if obj.cashed else '✗'
    cashed_display.short_description = 'Cashed'
    
    def top_10_display(self, obj):
        return '✓' if obj.top_10_percent else '✗'
    top_10_display.short_description = 'Top 10%'


@admin.register(NbaPlayerPerformance)
class NbaPlayerPerformanceAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'slate', 'team', 'position',
        'salary', 'projected_points', 'actual_points',
        'diff_display', 'value_actual'
    )
    list_filter = ('slate__site', 'position', 'team', 'slate__date')
    search_fields = ('name', 'dfs_id', 'team')
    date_hierarchy = 'created_at'
    ordering = ('-actual_points',)
    
    readonly_fields = ('value_projected', 'value_actual', 'projection_diff', 'outperformed')
    
    def diff_display(self, obj):
        diff = obj.projection_diff
        if diff is not None:
            color = 'green' if diff > 0 else 'red'
            sign = '+' if diff > 0 else ''
            return format_html(
                '<span style="color: {}; font-weight: bold;">{}{:.1f}</span>',
                color, sign, diff
            )
        return '-'
    diff_display.short_description = 'Diff'


@admin.register(OwnershipModel)
class OwnershipModelAdmin(admin.ModelAdmin):
    list_display = (
        'slate', 'model_version', 'training_samples',
        'mae_display', 'rmse_display', 'r2_display',
        'trained_at'
    )
    list_filter = ('model_version', 'slate__site')
    search_fields = ('slate__date', 'model_version')
    date_hierarchy = 'trained_at'
    
    readonly_fields = ('trained_at',)
    
    def mae_display(self, obj):
        if obj.mean_absolute_error:
            return f"{obj.mean_absolute_error:.2f}"
        return '-'
    mae_display.short_description = 'MAE'
    
    def rmse_display(self, obj):
        if obj.root_mean_squared_error:
            return f"{obj.root_mean_squared_error:.2f}"
        return '-'
    rmse_display.short_description = 'RMSE'
    
    def r2_display(self, obj):
        if obj.r_squared:
            return f"{obj.r_squared:.3f}"
        return '-'
    r2_display.short_description = 'R²'


@admin.register(PlayerCorrelation)
class PlayerCorrelationAdmin(admin.ModelAdmin):
    list_display = (
        'player1_name', 'player2_name',
        'corr_display', 'games_together',
        'same_team', 'avg_combined_points'
    )
    list_filter = ('same_team', 'position_group')
    search_fields = ('player1_name', 'player2_name')
    ordering = ('-correlation_coefficient',)
    
    def corr_display(self, obj):
        corr = obj.correlation_coefficient
        if corr > 0.5:
            color = 'green'
        elif corr < -0.5:
            color = 'red'
        else:
            color = 'gray'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.3f}</span>',
            color, corr
        )
    corr_display.short_description = 'Correlation'


@admin.register(StrategyPerformance)
class StrategyPerformanceAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'total_lineups',
        'invested_display', 'won_display', 'roi_display',
        'cash_rate_display', 'top_10_display'
    )
    search_fields = ('name', 'description')
    ordering = ('-total_won',)
    
    readonly_fields = ('roi',)
    
    def invested_display(self, obj):
        return f"${obj.total_invested:,.2f}"
    invested_display.short_description = 'Invested'
    
    def won_display(self, obj):
        return f"${obj.total_won:,.2f}"
    won_display.short_description = 'Won'
    
    def roi_display(self, obj):
        roi = obj.roi
        color = 'green' if roi > 0 else 'red' if roi < 0 else 'gray'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.1f}%</span>',
            color, roi
        )
    roi_display.short_description = 'ROI'
    
    def cash_rate_display(self, obj):
        return f"{obj.cash_rate:.1f}%"
    cash_rate_display.short_description = 'Cash Rate'
    
    def top_10_display(self, obj):
        return f"{obj.top_10_rate:.1f}%"
    top_10_display.short_description = 'Top 10%'