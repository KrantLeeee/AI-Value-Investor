"""Tests for sentiment analysis functions."""


def test_build_sentiment_context():
    """Test sentiment context includes headlines"""
    from src.agents.sentiment import build_sentiment_context

    news_items = [
        {'title': '公司营收创新高', 'date': '2026-03-10', 'source': '财联社'},
        {'title': '公司获得重大订单', 'date': '2026-03-09', 'source': '证券时报'},
        {'title': '公司遭遇监管调查', 'date': '2026-03-08', 'source': '每日经济'},
    ]

    context = build_sentiment_context(news_items, max_headlines=10)

    assert context['news_count'] == 3
    assert len(context['news_headlines']) == 3
    assert context['news_headlines'][0]['title'] == '公司营收创新高'
    assert 'sentiment_hint' in context['news_headlines'][0]


def test_classify_headline_sentiment():
    """Test headline sentiment classification"""
    from src.agents.sentiment import classify_headline_sentiment

    assert classify_headline_sentiment('公司营收增长超预期') == 'positive'
    assert classify_headline_sentiment('公司亏损扩大') == 'negative'
    assert classify_headline_sentiment('公司发布年报') == 'neutral'
