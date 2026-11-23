# Quantitative methods · 量化方法速查

Browse by research question below. For the interactive methods map, run the [relationship visualizer](../prior_knowledge/Relationships/relationship_visualizer.py) and open `knowledge_base/math/methods.html`. JSON entries have stable IDs and typed links. The `repository_example` field points to a local implementation or study. Other entries serve as background references unless a linked study documents their use.

## Signals & features · 信号与特征

What can be measured before a decision?

- [Announcement surprise](feature/earnings_surprise.json) · 公告预期差
- [Average true range (ATR)](feature/atr.json) · 真实波幅
- [Bollinger Bands](feature/bollinger.json) · 布林带
- [Cointegration and spread construction](feature/cointegration.json) · 协整与价差
- [Cross-sectional factor sorts](feature/factor_sorts.json) · 横截面因子分组
- [Factor neutralization](feature/cross_sectional_neutralization.json) · 因子中性化
- [Implied volatility inversion](feature/implied_volatility.json) · 期权隐含波动率反解
- [Moving-average trend](feature/moving_average.json) · 均线趋势
- [Option gamma exposure](feature/option_gamma.json) · 期权 Gamma 敞口
- [Order flow imbalance](feature/order_imbalance.json) · 订单流失衡
- [Price momentum (ROC)](feature/momentum.json) · 价格动量
- [Principal component analysis (PCA)](feature/principal_components.json) · 主成分分析
- [Relative strength index (RSI)](feature/rsi.json) · 相对强弱指标
- [Text sentiment scoring](feature/text_sentiment.json) · 文本情绪打分
- [Volatility term structure](feature/volatility_curve.json) · 波动率期限结构

## Training labels · 训练标签

Which future outcome should the model learn?

- [Benchmark-relative return](labeling/benchmark_relative.json) · 相对基准收益
- [Beta-adjusted return](labeling/residual_return.json) · Beta 调整收益
- [Fixed-horizon return](labeling/forward_return.json) · 固定期限收益
- [Forward loss from entry](labeling/forward_downside.json) · 未来相对入场价的最深跌幅 · `forward_drawdown` in registrations
- [Forward-average return](labeling/forward_average.json) · 未来平均收益
- [Triple-barrier labeling](labeling/triple_barrier.json) · 三重屏障标注

## Predictors · 预测模型

How can inputs be mapped to a score, distribution or state?

- [ARCH / GARCH volatility](predictor/garch.json) · 条件波动率模型
- [ARIMA / SARIMAX](predictor/arima.json) · 自回归与季节性时间序列
- [GBDT + CORAL (proposed Model B)](predictor/gbdt_coral.json) · 共享分数的有序提升树
- [Gradient-boosted tree classifier](predictor/gradient_boosted_trees.json) · 梯度提升树
- [Linear regression and regularization](predictor/linear_regression.json) · 线性回归与正则化
- [Logistic regression](predictor/logistic.json) · 逻辑回归
- [Markov regime switching](predictor/regime_switching.json) · 马尔可夫状态切换
- [Nearest-neighbour forecasting](predictor/nearest_neighbors.json) · 近邻情景预测
- [Ordinal classification (ordered logit / CORAL)](predictor/ordinal_classifier.json) · 有序分类
- [Quantile regression](predictor/quantile_regression.json) · 分位数回归
- [Random forests](predictor/random_forest.json) · 随机森林
- [Recurrent sequence models (LSTM / GRU)](predictor/neural_sequence.json) · 循环神经网络
- [State-space models and Kalman filtering](predictor/state_space.json) · 状态空间与卡尔曼滤波
- [Support vector machines (SVC / SVR)](predictor/svm.json) · 支持向量机
- [Threshold-expanded ordinal trees](predictor/ordinal_boosted_trees.json) · 阈值展开的有序树

## Risk measures · 风险度量

Which exposures and losses should be measured?

- [Covariance shrinkage](risk/covariance_shrinkage.json) · 协方差收缩估计
- [Maximum drawdown](risk/drawdown.json) · 最大回撤
- [Monte Carlo simulation](risk/monte_carlo.json) · 蒙特卡洛模拟
- [Realized volatility](risk/realized_vol.json) · 已实现波动率
- [Rolling market beta](risk/rolling_beta.json) · 滚动市场 Beta
- [Scenario and stress testing](risk/stress_testing.json) · 情景与压力测试
- [Value at risk and expected shortfall](risk/var_es.json) · 风险价值与预期损失

## Valuation · 衍生品定价

How does the contract translate a market scenario into a price?

- [Black–Scholes–Merton pricing](valuation/black_scholes.json) · 欧式期权解析定价
- [Binomial option pricing](valuation/binomial_tree.json) · 二叉树期权定价

## Strategies · 策略构造

Which trading rule turns a measured effect into holdings?

- [Carry strategies](strategy/carry.json) · 持有收益策略
- [Long-short factor portfolios](strategy/factor_portfolio.json) · 多空因子组合
- [Pairs trading and mean reversion](strategy/pairs_trading.json) · 配对与均值回归
- [Time-series trend following](strategy/trend_following.json) · 趋势跟随

## Portfolio construction · 组合与仓位

How should positions or capital be allocated?

- [Conditional value-at-risk allocation](portfolio/cvar.json) · 尾部损失优化配置
- [Equal weight (1/N)](portfolio/equal_weight.json) · 等权组合
- [Mean-variance allocation](portfolio/mean_variance.json) · 均值方差配置
- [Risk budgeting and risk parity](portfolio/risk_budgeting.json) · 风险预算与风险平价
- [Rule-based exposure gate](portfolio/rule_gate.json) · 规则仓位控制
- [Volatility targeting](portfolio/vol_target.json) · 目标波动率仓位

## Numerical optimization · 数值优化

Which numerical procedure solves the objective?

- [Newton and quasi-Newton methods](optimizer/newton.json) · 牛顿法与拟牛顿法
- [Quadratic programming (QP)](optimizer/quadratic_programming.json) · 二次规划
- [Stochastic gradient descent and Adam](optimizer/adam.json) · 随机梯度下降与 Adam

## Evaluation · 验证与回测

How should performance and uncertainty be assessed?

- [Permutation feature importance](evaluation/permutation_importance.json) · 置换特征重要性
- [Episode bootstrap](evaluation/episode_bootstrap.json) · 按风险事件重采样
- [Event study](evaluation/event_study.json) · 事件研究
- [Factor return attribution](evaluation/factor_attribution.json) · 因子收益归因
- [Matched feature ablation](evaluation/feature_ablation.json) · 同条件因子消融
- [Probability calibration](evaluation/calibration.json) · 概率校准
- [Purged walk-forward evaluation](evaluation/walkforward.json) · 清除重叠标签的滚动前推
- [Rank information coefficient (IC)](evaluation/information_coefficient.json) · 横截面秩相关
- [Sharpe ratio](evaluation/sharpe.json) · 夏普比率
- [Tail recall and alert rate](evaluation/tail_recall.json) · 尾部召回率与预警率

## Execution · 交易执行

How are intended positions translated into trades and costs?

- [Transaction costs and market impact](execution/impact.json) · 交易成本与市场冲击
- [VWAP and TWAP execution](execution/vwap.json) · 成交量与时间加权执行

## Tools · 工具

Which software implements a method?

- [CVXPY](tools/cvxpy.json) · 凸优化建模工具
- [LightGBM](tools/lightgbm.json) · 梯度提升树工具
- [scikit-learn](tools/scikit_learn.json) · 通用机器学习工具
- [statsmodels](tools/statsmodels.json) · 统计建模工具
- [TA-Lib](tools/ta_lib.json) · 技术指标工具
