# Fix: Blank Page Issue

## The Problem

You're seeing a blank page when accessing the app.

## The Solution

**Use the correct URL:**

✅ **Correct:** `http://localhost:8501`  
❌ **Wrong:** `http://0.0.0.0:8501`

## Why This Happens

The logs show `http://0.0.0.0:8501` because that's the **internal Docker address**. From your browser, you must use `localhost` or `127.0.0.1`.

## Quick Fix

1. **Open your browser**
2. **Type:** `http://localhost:8501`
3. **Press Enter**

The app should load!

## Verify It's Working

```bash
# Check if app is responding
curl http://localhost:8501

# Should return HTML (not an error)
```

## Still Not Working?

If `http://localhost:8501` still shows a blank page:

1. **Check container status:**
   ```bash
   docker-compose ps
   ```
   Both containers should show "Up" and "healthy"

2. **Check app logs for errors:**
   ```bash
   docker-compose logs app --tail 50
   ```

3. **Try restarting:**
   ```bash
   docker-compose restart app
   ```

4. **Check browser console:**
   - Open browser developer tools (F12)
   - Check Console tab for JavaScript errors
   - Check Network tab for failed requests

## Alternative URLs to Try

If `localhost` doesn't work, try:
- `http://127.0.0.1:8501`
- `http://0.0.0.0:8501` (only works from inside container, not browser)

---

**Remember:** Always use `http://localhost:8501` from your browser! 🚀

