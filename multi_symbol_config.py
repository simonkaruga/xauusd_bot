"""
Multi-Symbol Trading Configuration

Trade multiple instruments to increase opportunities and reduce risk
"""

TRADING_SYMBOLS = {
    'XAUUSD': {
        'enabled': True,
        'risk_allocation': 0.40,  # 40% of capital
        'contract_size': 100,
        'min_lot': 0.01,
        'spread_avg': 3.5,
        'volatility': 'high',
        'best_sessions': ['london', 'newyork']
    },
    'EURUSD': {
        'enabled': True,
        'risk_allocation': 0.25,
        'contract_size': 100000,
        'min_lot': 0.01,
        'spread_avg': 1.5,
        'volatility': 'medium',
        'best_sessions': ['london', 'newyork']
    },
    'GBPUSD': {
        'enabled': True,
        'risk_allocation': 0.20,
        'contract_size': 100000,
        'min_lot': 0.01,
        'spread_avg': 2.0,
        'volatility': 'high',
        'best_sessions': ['london']
    },
    'USDJPY': {
        'enabled': False,  # Enable after testing
        'risk_allocation': 0.15,
        'contract_size': 100000,
        'min_lot': 0.01,
        'spread_avg': 1.8,
        'volatility': 'medium',
        'best_sessions': ['tokyo', 'london']
    }
}

# Trading Sessions (GMT)
SESSIONS = {
    'tokyo': {'start': 0, 'end': 9},
    'london': {'start': 8, 'end': 17},
    'newyork': {'start': 13, 'end': 22}
}

def get_active_symbols(current_hour):
    """Return symbols that should be traded in current session"""
    active = []
    
    for symbol, config in TRADING_SYMBOLS.items():
        if not config['enabled']:
            continue
        
        for session in config['best_sessions']:
            session_hours = SESSIONS[session]
            if session_hours['start'] <= current_hour < session_hours['end']:
                active.append(symbol)
                break
    
    return list(set(active))
