from series_summation import series_to_bound
from mathematica_export import inequality

# Define your example series objects here. Add more as needed.
series_1 = series_to_bound(
    formula="(2*d+1)/(2*h^2*(1+d*(d+1)/(h^2))(1+d*(d+1)/(h^2*m^2))^2)",
    conditions="h >1 && m > 1",
    summation_index="d",
    other_variables="{h,m}",
    summation_bounds=["0", "Infinity"],
    conjectured_upper_asymptotic_bound="1+Log[m^2]",
)
#\sum_{d=1}^{\infty} 

series_2 = series_to_bound(
    formula="a/d^2",
    conditions="a>1 && d>1",
    summation_index="d",
    other_variables="{a}",
    summation_bounds=["1", "Infinity"],
    conjectured_upper_asymptotic_bound="a",
)

series_3 = series_to_bound(
    formula="1/d",
    conditions="True",
    summation_index="d",
    other_variables="True",
    summation_bounds=["1", "Infinity"],
    conjectured_upper_asymptotic_bound="1",
)

series_4 = series_to_bound(
    formula="1/d^4",
    conditions="True",
    summation_index="d",
    other_variables="True",
    summation_bounds=["1", "Infinity"],
    conjectured_upper_asymptotic_bound="1",
)

series_5 = series_to_bound(
    formula="1/d^6",
    conditions="True",
    summation_index="d",
    other_variables="True",
    summation_bounds=["1", "Infinity"],
    conjectured_upper_asymptotic_bound="1",
)

series_6 = series_to_bound(
    formula="1/(2^d + a/2^d)",
    conditions="a>=2",
    summation_index="d",
    other_variables="{a}",
    summation_bounds=["-Infinity", "Infinity"],
    conjectured_upper_asymptotic_bound="Log[a]",
)

series_7 = series_to_bound(
    formula="2^d",
    conditions="True",
    summation_index="d",
    other_variables="True",
    summation_bounds=["-Infinity", "-1"],
    conjectured_upper_asymptotic_bound="1",
)

series_8 = series_to_bound(
    formula="1/(2^n + a/2^n)",
    conditions="a>=2",
    summation_index="n",
    other_variables="{a}",
    summation_bounds=["-Infinity", "Infinity"],
    conjectured_upper_asymptotic_bound="Log[a]",
)

series_9 = series_to_bound(
    formula="1/d",
    conditions="True",
    summation_index="d",
    other_variables="True",
    summation_bounds=["1", "Infinity"],
    conjectured_upper_asymptotic_bound="1",
)

series_10 = series_to_bound(
    formula="Exp[-d^2/4]",
    conditions="True",
    summation_index="d",
    other_variables="True",
    summation_bounds=["-Infinity", "Infinity"],
    conjectured_upper_asymptotic_bound="2",
)

series_11 = series_to_bound(
    formula="Exp[-d^2/a^2]",
    conditions="a>1",
    summation_index="d",
    other_variables="{a}",
    summation_bounds=["-Infinity", "Infinity"],
    conjectured_upper_asymptotic_bound="a",
)









inequality_1 = inequality(
    variables="{x,y}",
    domain_description="{y>0, x>1}",
    lhs="x*y",
    rhs="x*Log[x]+Exp[y]",
)

inequality_2 = inequality(
    variables = "x,y,z", 
    domain_description = "x>0, y>0, z>0", 
    lhs = "(x*y*z)^(1/3)", 
    rhs = "(x+y+z)/3"
    )

inequality_3 = inequality(
    variables = "x,y,z", 
    domain_description = "x>0, y>0", 
    lhs = "(x*y)^(1/2)", 
    rhs = "(x+y)/2"
    )

inequality_4 = inequality(
    variables = "x", 
    domain_description = "x>1", 
    lhs = "x^51", 
    rhs = "x"
    )
#Should return False. 

inequality_5 = inequality(
    variables = "x", 
    domain_description = "x>1", 
    lhs = "x^2", 
    rhs = "x"
    )
#Should return False. 

inequality_6 = inequality(
    variables = "x", 
    domain_description = "x>1", 
    lhs = "x^3", 
    rhs = "x"
    )
#Should return False. 

