package com.dotamodel.app;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Dota 2 Model for Android: the predictor page (app.html, built by build.py) is inside the app, so it opens
 * without a server and works offline. When online it loads the newest model.js (published daily by the
 * GitHub workflow) and live Polymarket prices.
 */
public class MainActivity extends Activity {
    private static final String PAGE = "file:///android_asset/index.html";
    private WebView web;

    @Override
    @SuppressWarnings("deprecation")
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.parseColor("#1A1412"));
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#1A1412"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // the scorecard of priced matches is kept in localStorage
        s.setAllowFileAccess(true);
        s.setAllowUniversalAccessFromFileURLs(true);  // the bundled page reads Polymarket's public API
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                return open(req.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return open(Uri.parse(url));
            }
        });
        setContentView(web, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        if (savedInstanceState != null) web.restoreState(savedInstanceState);
        else web.loadUrl(PAGE);
    }

    /** Links to other sites (Polymarket, Liquipedia...) open in the browser; the app's own page stays here. */
    private boolean open(Uri url) {
        if ("file".equals(url.getScheme())) return false;
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, url));
        } catch (Exception ignored) {
        }
        return true;
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
