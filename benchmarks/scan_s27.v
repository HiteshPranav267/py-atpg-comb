// Scan inserted s27
module sdff (CK,Q,D,SI,SE);
input CK,D,SI,SE;
output Q;
reg Q;
always @ (posedge CK)
  Q <= SE ? SI : D;
endmodule

module s27(CK,G0,G1,G17,G2,G3,SI,SE,SO);
input CK,G0,G1,G2,G3,SI,SE;
output G17,SO;

  wire G5,G10,G6,G11,G7,G13,G14,G8,G15,G12,G16,G9;

  sdff DFF_0(CK,G5,G10,SI,SE);
  sdff DFF_1(CK,G6,G11,G5,SE);
  sdff DFF_2(CK,G7,G13,G6,SE);
  buf BUF_SO(SO,G7);
  
  not NOT_0(G14,G0);
  not NOT_1(G17,G11);
  and AND2_0(G8,G14,G6);
  or OR2_0(G15,G12,G8);
  or OR2_1(G16,G3,G8);
  nand NAND2_0(G9,G16,G15);
  nor NOR2_0(G10,G14,G11);
  nor NOR2_1(G11,G5,G9);
  nor NOR2_2(G12,G1,G7);
  nor NOR2_3(G13,G2,G12);

endmodule
