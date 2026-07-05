
#!/usr/bin/env python3
"""
Apply the fix safely with Python.
"""
import re

file_path = '/root/crypto-trading-open/core/adapters/exchanges/adapters/binance_rest.py'

# Read the file
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the create_order function
create_order_pattern = re.compile(
    r'(    async def create_order\(.*?\) -&gt; OrderData:.*?)(\n    async def create_batch_orders)',
    re.DOTALL
)

# Replacement code for create_order
replacement = '''    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: Decimal,
        price: Optional[Decimal] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> OrderData:
        """创建订单"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol)
            
            # 准备订单参数
            order_params = params or {}
            
            # Adjust amount to meet minimum notional
            if price is not None:
                amount = self._adjust_min_notional(mapped_symbol, amount, price)
            
            order_data = await self._execute_with_retry(
                self.exchange.create_order,
                mapped_symbol,
                self.ORDER_TYPE_MAPPING.get(order_type, 'LIMIT'),
                side.value.lower(),
                float(amount),
                float(price) if price else None,
                order_params
            )
            
            return self.parse_order(order_data, symbol)
        except Exception as e:
            if self.logger:
                self.logger.error(f"创建订单失败 {symbol}: {str(e)}")
            raise
    
    def _adjust_min_notional(self, mapped_symbol, amount, price):
        """Adjust order amount to meet minimum notional requirement."""
        if not self._market_info or mapped_symbol not in self._market_info:
            return amount
        
        market = self._market_info[mapped_symbol]
        min_notional = Decimal('20')  # Default from error
        
        # Get min notional from limits
        limits = market.get('limits', {})
        cost_limit = limits.get('cost', {})
        if cost_limit and cost_limit.get('min'):
            min_notional = Decimal(str(cost_limit['min']))
        else:
            # Get from filters
            info = market.get('info', {})
            filters = info.get('filters', [])
            for f in filters:
                ft = f.get('filterType')
                if ft == 'MIN_NOTIONAL' and 'notional' in f:
                    min_notional = Decimal(str(f['notional']))
                    break
                if ft == 'NOTIONAL' and 'minNotional' in f:
                    min_notional = Decimal(str(f['minNotional']))
                    break
        
        notional = amount * price
        if notional &gt;= min_notional:
            return amount
        
        # Calculate required amount
        req_amount = (min_notional / price).quantize(Decimal('1.00000000'))
        
        # Get step size
        step = Decimal('0.0001')
        filters = market.get('info', {}).get('filters', [])
        for f in filters:
            if f.get('filterType') == 'LOT_SIZE':
                step = Decimal(str(f.get('stepSize', '0.0001')))
                break
        
        # Round up
        req_amount = ((req_amount + step - Decimal('1e-9')) // step) * step
        
        if self.logger:
            self.logger.warning(
                "Adjusting order: orig=%s @ %s = %.2f, min=%s, new=%s"
                % (amount, price, float(notional), min_notional, req_amount)
            )
        
        return req_amount

    async def create_batch_orders'''

# Now let's use actual comparison operators, not HTML entities!
replacement = replacement.replace('&gt;=', '>=')

# Apply fix to create_order
content = create_order_pattern.sub(replacement, content)

# Now fix create_batch_orders as well
batch_pattern = re.compile(
    r'(            for o in batch:\s+)(ccxt_orders.append\(\{)',
    re.DOTALL
)

batch_replacement = '''            for o in batch:
                o_amount = o['amount']
                o_price = o.get('price')
                if o_price is not None:
                    o_amount = self._adjust_min_notional(mapped_symbol, o_amount, o_price)
                ccxt_orders.append({'''

content = batch_pattern.sub(batch_replacement, content)

# Now fix the amount line in batch
amount_pattern = re.compile(r"'amount': float\(o\['amount'\]\),")
content = amount_pattern.sub("'amount': float(o_amount),", content)

# Write back to file
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Fix applied successfully!")
