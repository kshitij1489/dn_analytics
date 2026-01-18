def format_indian_currency(number):
    """Format number with Indian nomenclature (Lakhs, Crores) without decimals"""
    try:
        if number is None: return "0"
        s = str(int(float(number)))
        if len(s) <= 3: return s
        last_three = s[-3:]
        others = s[:-3]
        others_reversed = others[::-1]
        pairs = [others_reversed[i:i+2] for i in range(0, len(others_reversed), 2)]
        formatted_others = ",".join(pairs)[::-1]
        return f"{formatted_others},{last_three}"
    except:
        return str(number)

def format_hour(h):
    """Format 24h integer to 12h string (e.g., 14 -> '2 PM')"""
    if h == 0 or h == 24: return "12 AM"
    if h < 12: return f"{h} AM"
    if h == 12: return "12 PM"
    return f"{h-12} PM"

def format_chart_value(val):
    """Format large numbers for charts (K/M/Cr)"""
    if val >= 10000000:
        return f"₹{val/10000000:.1f}Cr"
    elif val >= 100000:
        return f"₹{val/100000:.1f}L"
    elif val >= 1000:
        return f"₹{val/1000:.1f}K"
    return f"₹{val}"
